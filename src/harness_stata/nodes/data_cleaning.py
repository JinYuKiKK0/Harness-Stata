"""数据清洗节点（F20）——工作流第六个节点。

基于 LangChain ``create_agent``,底座改用 DuckDB。消费 DownloadedFiles（F18）与
EmpiricalSpec（F09），打开一个内存 DuckDB 连接，把每份下载的 CSV 预先注册为
``src_<source_table>`` 视图；把唯一的 ``run_sql`` 工具绑定给 agent,由 LLM 写
SQL 完成清洗/连接/宽长转换,再由节点执行后置校验并写入 ``merged_dataset``。

失败分层：主键重复、final_view 缺失、ReAct 超轮截断 -> RuntimeError；
变量覆盖率低于 ``Settings.cleaning_coverage_threshold`` -> 仅进入
``MergedDataset.warnings``。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd
from duckdb import DuckDBPyConnection
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from harness_stata.config import get_settings
from harness_stata.nodes._agent_runner import run_structured_agent
from harness_stata.nodes._writes import awrites_to
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DownloadedFile,
    EmpiricalSpec,
    MergedDataset,
    VariableDefinition,
    WorkflowState,
)

_LOGGER = logging.getLogger(__name__)

_MAX_ITERATIONS = 40
_MERGED_FILENAME = "merged.csv"
_STAGE_DIRNAME = "_stage"
_SRC_PREFIX = "src_"
_PREVIEW_ROWS = 20
_SAMPLE_ROWS = 3
# 严格的 SQL 标识符白名单：ASCII 字母/数字/下划线，且不以数字开头。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# DuckDB 对 DDL/DML/SET 会返回 0~1 行的 'Count' 或 'Success' 元信息列。
_META_RESULT_COLUMNS = frozenset({"Count", "Success"})


class _CleaningOutput(BaseModel):
    """LLM-facing structured-output schema for the data_cleaning terminal step."""

    final_view: str = Field(description="The final merged view or table name in DuckDB.")
    primary_key: list[str] = Field(description="Primary key column names of final_view.")


def _validate(state: WorkflowState) -> str | None:
    downloaded = state.get("downloaded_files")
    if downloaded is None or not downloaded.get("files"):
        return "state.downloaded_files.files is missing or empty"
    if state.get("empirical_spec") is None:
        return "state.empirical_spec is missing"
    return None


def _derive_output_path(files: list[DownloadedFile]) -> Path:
    """把合并产物放在 F18 的会话目录下：``<session>/merged.csv``。

    ``files[*].path`` 已由 F18 保证为绝对路径，此处刻意不调用
    ``Path.resolve()``——Windows 下 resolve 会在事件循环里触发
    ``os.getcwd()``，被 ``langgraph dev`` 的 blockbuster 拦截。
    """
    first = Path(files[0]["path"])
    return first.parents[1] / _MERGED_FILENAME


def _format_variables(variables: list[VariableDefinition]) -> str:
    return "\n".join(
        f"- {v['name']} ({v['role']}, {v['contract_type']}): {v['description']}" for v in variables
    )


def _register_sources(conn: DuckDBPyConnection, files: list[DownloadedFile]) -> list[str]:
    """把每个下载文件注册为 ``src_<source_table>`` 视图。

    视图落在 ``main`` schema。``source_table`` 必须先通过 :data:`_IDENT_RE`
    白名单校验，才能进入 SQL 标识符位置拼接。文件路径通过 DuckDB Python
    relation API（``conn.read_csv``）传入，避免任何字符串级 SQL 拼接。

    返回按输入顺序排列的视图名列表。
    """
    view_names: list[str] = []
    for f in files:
        source_table = f["source_table"]
        if not _IDENT_RE.match(source_table):
            msg = (
                f"data_cleaning: illegal source_table {source_table!r};"
                f" must match {_IDENT_RE.pattern}"
            )
            raise RuntimeError(msg)
        view_name = f"{_SRC_PREFIX}{source_table}"
        path = Path(f["path"])
        suffix = path.suffix.lower()
        if suffix == ".csv":
            rel = conn.read_csv(str(path))
        elif suffix in (".xlsx", ".xls"):
            msg = (
                f"data_cleaning: xlsx support deferred; upstream must emit CSV for MVP"
                f" (got {path.name})"
            )
            raise NotImplementedError(msg)
        else:
            msg = f"data_cleaning: unsupported source format {suffix!r} for {path.name}"
            raise ValueError(msg)
        conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
        rel.create_view(view_name)
        view_names.append(view_name)
    return view_names


def _probe_sources_for_prompt(
    conn: DuckDBPyConnection,
    files: list[DownloadedFile],
    view_names: list[str],
) -> list[str]:
    """为每个已注册视图采集 schema + 前 3 行样本，拼成可注入 HumanMessage 的描述块。

    提前把 schema 与样本注入 prompt，可以把 LLM 原本花在 DESCRIBE/SELECT LIMIT
    上的若干 ReAct 回合省掉，直接进入写清洗 SQL 的阶段。
    """
    blocks: list[str] = []
    for i, (f, view_name) in enumerate(zip(files, view_names, strict=True), start=1):
        schema_df = conn.execute(f'DESCRIBE "{view_name}"').fetchdf()
        schema_lines = [
            f"     - {row['column_name']}: {row['column_type']}" for _, row in schema_df.iterrows()
        ]
        sample_df = conn.execute(f'SELECT * FROM "{view_name}" LIMIT {_SAMPLE_ROWS}').fetchdf()
        sample_txt = sample_df.to_string(index=False) if len(sample_df) > 0 else "(empty table)"
        block = (
            f"{i}. path={f['path']}\n"
            f"   source_table={f['source_table']}  (registered view: {view_name})\n"
            f"   key_fields={f['key_fields']}\n"
            f"   variable_names={f['variable_names']}\n"
            f"   schema:\n"
            + "\n".join(schema_lines)
            + f"\n   sample (first {_SAMPLE_ROWS} rows):\n{sample_txt}"
        )
        blocks.append(block)
    return blocks


def _build_human_prompt(
    spec: EmpiricalSpec,
    source_blocks: list[str],
    output_path: Path,
) -> str:
    return (
        f"## topic\n{spec['topic']}\n\n"
        f"## analysis_granularity\n{spec['analysis_granularity']}\n\n"
        f"## sample / time / frequency\n"
        f"sample_scope: {spec['sample_scope']}\n"
        f"time_range: {spec['time_range_start']} - {spec['time_range_end']}\n"
        f"data_frequency: {spec['data_frequency']}\n\n"
        f"## variables (EmpiricalSpec.variables)\n"
        f"{_format_variables(spec['variables'])}\n\n"
        f"## pre-registered source views\n" + "\n\n".join(source_blocks) + "\n\n"
        f"## output_path (node will export final_view here; do NOT COPY yourself)\n"
        f"{output_path}\n\n"
        "Build a single long-format view/table that merges the sources, then return"
        " the final_view name and primary_key columns via the structured output schema."
    )


def _format_query_result(df: pd.DataFrame) -> str:
    """把 DuckDB 结果 DataFrame 渲染成供 LLM 阅读的预览字符串。"""
    total = len(df)
    cols = list(df.columns)
    # DuckDB 对 DDL/DML/SET 会返回 0~1 行的 'Count'/'Success' 元结果。
    # 折叠成 "OK"，避免给 LLM 塞进误导性的元行。
    if len(cols) == 1 and cols[0] in _META_RESULT_COLUMNS:
        if total == 0:
            return "OK"
        return f"OK (affected rows: {int(df.iat[0, 0])})"
    if total == 0:
        return "(no rows)"
    preview = df.head(_PREVIEW_ROWS).to_string(index=False)
    if total <= _PREVIEW_ROWS:
        return f"{preview}\n(total rows: {total})"
    return f"{preview}\n... ({total - _PREVIEW_ROWS} more rows; total: {total})"


def _make_sql_tool(conn: DuckDBPyConnection) -> BaseTool:
    """构建一个绑定到给定 DuckDB 连接的 ``run_sql`` 工具。"""

    @tool
    def run_sql(query: str) -> str:
        """在共享的内存 DuckDB 连接上执行一条 SQL。

        可直接查询的预注册视图：每份下载文件对应一个
        ``src_<source_table>`` 视图。标准 SQL 全部可用（SELECT /
        CREATE VIEW / CREATE TABLE / DESCRIBE 等）。

        返回规则：SELECT / DESCRIBE 返回前 20 行预览 + 总行数；
        DDL / DML / SET 折叠为 ``"OK"``（或 ``"OK (affected rows: N)"``）；
        SQL 错误返回 ``"ERROR: <类型>: <消息>"`` 字符串，工具自身不抛异常，
        以便 Agent 根据错误自行修正重跑。
        """
        try:
            cursor = conn.execute(query)
            if cursor is None:
                # DuckDB 对 comment-only / 空语句返回 None，避免后续 .fetchdf() 炸 AttributeError
                return "OK (no executable statement)"
            df = cursor.fetchdf()
        except duckdb.Error as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"
        return _format_query_result(df)

    return run_sql


def _list_intermediate_relations(conn: DuckDBPyConnection) -> list[str]:
    """返回 ``main`` schema 下所有非 ``src_`` 前缀的表/视图名。"""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'main' AND table_name NOT LIKE ?"
        " ORDER BY table_name",
        [f"{_SRC_PREFIX}%"],
    ).fetchall()
    return [str(r[0]) for r in rows]


def _dump_intermediate_artifacts(conn: DuckDBPyConnection, stage_dir: Path) -> list[str]:
    """尽力而为地把每个非 ``src_`` 关系落盘到 ``<stage_dir>/<name>.csv``。

    包含 LLM CREATE 后未 DROP 的失败尝试在内的所有中间态，都会被 dump，
    方便开发者事后调试。任何单个文件 dump 失败仅记日志、不抛异常——
    中间产物是调试辅助，不能阻断主流程。
    """
    dumped: list[str] = []
    for name in _list_intermediate_relations(conn):
        if not _IDENT_RE.match(name):
            _LOGGER.warning("data_cleaning: skipping dump of %r (non-identifier name)", name)
            continue
        dump_path = stage_dir / f"{name}.csv"
        try:
            conn.sql(f'SELECT * FROM "{name}"').write_csv(str(dump_path), header=True)
        except (duckdb.Error, OSError) as exc:
            _LOGGER.warning("data_cleaning: failed to dump intermediate %r: %s", name, exc)
            continue
        dumped.append(str(dump_path))
    return dumped


def _check_final_view_exists(conn: DuckDBPyConnection, view_name: str) -> None:
    if not _IDENT_RE.match(view_name):
        msg = (
            f"data_cleaning: final_view {view_name!r} is not a legal SQL identifier"
            f" (must match {_IDENT_RE.pattern})"
        )
        raise RuntimeError(msg)
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
        [view_name],
    ).fetchone()
    if row is None:
        msg = f"data_cleaning: final_view {view_name!r} not found in DuckDB connection"
        raise RuntimeError(msg)


def _export_final_view(conn: DuckDBPyConnection, view_name: str, output_path: Path) -> None:
    """将 ``final_view`` 以 CSV（带表头、逗号分隔）写到 ``output_path``。

    调用前 ``view_name`` 必须已通过 :func:`_check_final_view_exists` 校验。
    """
    conn.sql(f'SELECT * FROM "{view_name}"').write_csv(str(output_path), header=True)


def _find_variable_column(var_name: str, columns: list[str]) -> str | None:
    target = var_name.lower().replace("_", "")
    for col in columns:
        if col.lower().replace("_", "") == target:
            return col
    return None


def _check_post_conditions(
    csv_path: Path,
    spec: EmpiricalSpec,
    primary_key: list[str],
    coverage_threshold: float,
) -> tuple[int, list[str], list[str]]:
    df = pd.read_csv(csv_path)
    row_count = len(df)
    columns = [str(c) for c in df.columns]

    missing_keys = [k for k in primary_key if k not in columns]
    if missing_keys:
        msg = (
            f"data_cleaning: primary_key columns {missing_keys!r} not present"
            f" in merged CSV columns {columns!r}"
        )
        raise RuntimeError(msg)
    dup_count = int(df.duplicated(subset=primary_key).sum())
    if dup_count > 0:
        msg = (
            f"data_cleaning: merged CSV has {dup_count} duplicate rows"
            f" on primary_key {primary_key!r}"
        )
        raise RuntimeError(msg)

    warnings: list[str] = []
    for var in spec["variables"]:
        col = _find_variable_column(var["name"], columns)
        if col is None:
            warnings.append(f"variable {var['name']!r} not found in merged CSV columns")
            continue
        if row_count == 0:
            warnings.append(f"variable {var['name']!r} column exists but CSV is empty")
            continue
        non_null = int(df[col].notna().sum())
        coverage = non_null / row_count
        if coverage < coverage_threshold:
            warnings.append(
                f"variable {var['name']!r} (column {col!r}) coverage"
                f" {coverage:.2%} < threshold {coverage_threshold:.0%}"
            )
    return row_count, columns, warnings


@awrites_to("merged_dataset")
async def data_cleaning(state: WorkflowState) -> MergedDataset:
    """通过 DuckDB 把 DownloadedFiles 合并为单一长表 CSV。

    打开内存 DuckDB 连接，把每份源 CSV 注册为 ``src_<source_table>`` 视图，
    绑定 ``run_sql`` 工具驱动 ``create_agent``;拿到 agent 声明的 ``final_view``
    后，把所有非 ``src_`` 中间产物 dump 到 ``_stage/`` 再把 final_view 导出为
    merged.csv，最后执行后置校验。
    """
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]
    files = state["downloaded_files"]["files"]
    output_path = _derive_output_path(files)
    stage_dir = output_path.parent / _STAGE_DIRNAME
    stage_dir.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(":memory:")
    try:
        view_names = _register_sources(conn, files)
        source_blocks = _probe_sources_for_prompt(conn, files, view_names)
        sql_tool = _make_sql_tool(conn)
        payload, _ = await run_structured_agent(
            tools=[sql_tool],
            system_prompt=load_prompt("data_cleaning"),
            output_schema=_CleaningOutput,
            human_message=_build_human_prompt(spec, source_blocks, output_path),
            max_iterations=_MAX_ITERATIONS,
            node_name="data_cleaning",
        )
        final_view = payload.final_view
        primary_key = list(payload.primary_key)
        if not final_view:
            raise RuntimeError("data_cleaning: structured_response.final_view is empty")
        if not primary_key:
            raise RuntimeError("data_cleaning: structured_response.primary_key is empty")

        _check_final_view_exists(conn, final_view)
        _dump_intermediate_artifacts(conn, stage_dir)
        _export_final_view(conn, final_view, output_path)
    finally:
        conn.close()

    threshold = get_settings().cleaning_coverage_threshold
    row_count, columns, warnings = _check_post_conditions(output_path, spec, primary_key, threshold)
    return {
        "file_path": str(output_path),
        "row_count": row_count,
        "columns": columns,
        "warnings": warnings,
    }
