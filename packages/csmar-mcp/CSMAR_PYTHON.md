## 一. 功能介绍

CSMAR-PYTHON 是基于Python语言的数据接口（目前仅支持Windows系统），以函数调用的形式实现CSMAR经济金融研究数据库数据查询、下载等功能，方便用户在进行数据分析、实证研究时高效获取CSMAR数据，仅限个人注册账号使用。

## 二. 初始配置

**1. 安装python3.6.X工具【压缩包】，并安装类库urllib3，websocket，websocket_client，pandas**

```bash
pip install urllib3
pip install websocket
pip install websocket_client
pip install pandas
```

**2. CSMAR-PYTHON安装方式**

- 下载CSMAR-PYTHON【压缩包】
- 解压至python安装目录下的\Lib\site-packages
  如:\Programs\Python36\Lib\site-packages

**系统中所有的函数返回数据格式默认都是json，可以通过列表打印类库(prettytable)来格式化数据以便查看**

```bash
pip install prettytable
```

## 三. 使用CSMAR-PYTHON

**1. 使用CSMAR-PYTHON**

```python
from csmarapi.CsmarService import CsmarService
csmar = CsmarService()
```

如果需要以表格形式展示数据，则需添加

```python
from csmarapi.ReportUtil import ReportUtil
```

**2. 函数详细介绍**

**2.1 用户登录函数：login(account,pwd,lang)**

```python
login(account,pwd,lang)
```

- account：用户名/已验证电话/已验证邮箱 (仅限个人注册账号)
- pwd：密码
- lang：可选参数 (0或1：0代表中文，1代表英文，默认为0)

```python
#例：
#csmar.login('134******83', 'a*****c','1')
csmar.login('134******83', 'a*****c')
```

**2.2 查看已购买的数据库名称：getListDbs()**

```python
#例：
database = csmar.getListDbs()
ReportUtil(database)
```

**2.3 查看已购买的数据表名称：getListTables(databaseName)**

- databaseName：数据库名称，可通过getListDbs()获得

```python
#例：
tables = csmar.getListTables('财务报表')
ReportUtil(tables)
```

**2.4 查看已购买数据表中的所有字段：getListFields(tableName)**

- tableName：表名称，可通过getListTables(databaseName)获得

```python
#例：
fields = csmar.getListFields('FS_Combas')
ReportUtil(fields)
```

**2.5 预览数据函数：preview(tableName)**

- tableName：表名称，可通过getListTables(databaseName)获得

```python
#例：
data = csmar.preview('FS_Combas')
ReportUtil(data)
```

**2.6 查询已购买的数据表记录条数：queryCount(columns,condition,tableName,startTime,endTime)**

- columns：字段的列表，如：['Stkcd','ShortName','Accper','Typrep','A001000000']
- condition：条件，类似SQL条件语句，如："Stkcd='000001'"，但不支持order by(该函数已有默认的排序方式)
- tableName：表名称，可通过getListTables(databaseName)查看
- startTime和endTime：时间关键字参数(非必填，如需填写格式为：YYYY-MM-DD): 下载数据时间区间的开始时间和结束时间

```python
#例：
csmar.queryCount( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas')
#csmar.queryCount( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas','2010-01-01','2019-12-31')
```

**2.7 查询已购买的数据表数据**

**返回json数据格式：query(columns,condition,tableName,startTime,endTime)**

**返回DataFrame数据格式：query_df(columns,condition,tableName,startTime,endTime)**

- 一次最多只能加载200,000条记录
- columns：字段的列表，如：['Stkcd','ShortName','Accper','Typrep','A001000000']
- condition：条件，类似SQL条件语句，如："Stkcd='000001'"，但不支持order by(该函数已有默认的排序方式)
  - 如超过20万记录的数据可使用limit进行分页查询,假设是40万条，需分两次进行条件设置
  - 如：第一次："Stkcd like'3%' limit 0,200000"，第二次："Stkcd like'3%' limit 200000,200000"
- tableName：表名称，可通过getListTables(databaseName)查看
- startTime和endTime：时间关键字参数(非必填，如需填写格式为：YYYY-MM-DD): 下载数据时间区间的开始时间和结束时间
- 同个查询条件30分钟内只允许执行一次，查询条件涉及到的参数是condition，startTime，endTime

```python
#例1：
data = csmar.query( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas')
#data = csmar.query( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas','2010-01-01','2019-12-31')
ReportUtil(data)
#例2:
data = csmar.query_df( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas')
#data = csmar.query_df( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas','2010-01-01','2019-12-31')
print(data)
```

**2.8 下载数据函数并获取打包结果函数：getPackResultExt(columns, condition, tableName, startTime,endTime)**

- columns：字段的列表，如：['Stkcd','ShortName','Accper','Typrep','A001000000']
- condition：条件，类似SQL条件语句，如："Stkcd='000001'"，但不支持order by(该函数已有默认的排序方式)
- tableName：表名称，可通过getListTables(databaseName)查看
- startTime和endTime：时间关键字参数(非必填，如需填写格式为：YYYY-MM-DD): 下载数据时间区间的开始时间和结束时间
- 同个查询条件30分钟内只允许执行一次，查询条件涉及到的参数是condition，startTime，endTime

```python
#例：
csmar.getPackResultExt( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas')
#csmar.getPackResultExt( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%'", 'FS_Combas','2010-01-01','2019-12-31')
```

**2.9 解压下载的数据包函数：unzipSingle(filePath)，压缩包路径为C:\csmardata\zip\xxx.zip**

- filePath：压缩包的绝对路径

```python
#例：
csmar.unzipSingle('c:\\csmardata\\zip\\778639194952077312.zip')
```

**2.10 加载文件数据函数：loadData(filePath,count)**

- filePath：文件的绝对路径(解压后的文件一般在C:\csmardata\xxx路径下 其中xxx是指下载数据包的名称)
- count：加载数据的记录条数，非必填参数

```python
#例：
csmar.loadData('c:\\csmardata\\778639194952077312\\FS_Combas.csv')
```

## 四. 示例

**1. 简单查询已购买数据表的内容**

```python
from csmarapi.CsmarService import CsmarService
from csmarapi.ReportUtil import ReportUtil
csmar = CsmarService()
csmar.login('134******83', 'a*****c')
data = csmar.query_df( ['Stkcd','ShortName','Accper','Typrep','A001000000'], "Stkcd like'3%' and Typrep='A'", 'FS_Combas','2018-01-01','2019-12-31')
print(data)
```

**2. 结果截图如下**

```text
       Stkcd      Accper Typrep    A001000000 ShortName
0     300755  2018-01-01      A  1.977754e+09      华致酒行
1     300757  2018-01-01      A  7.074176e+08      罗博特科
2     300758  2018-01-01      A  5.910147e+08      七彩化学
3     300759  2018-01-01      A  3.919174e+09      康龙化成
4     301192  2018-01-01      A  2.602790e+08      泰祥股份
...      ...         ...    ...           ...       ...
7588  300334  2019-12-31      A  2.020568e+09      津膜科技
7589  300462  2019-12-31      A  3.238906e+09      华铭智能
7590  300433  2019-12-31      A  4.702855e+10      蓝思科技
7591  300301  2019-12-31      A  3.259027e+09      长方集团
7592  300208  2019-12-31      A  5.446574e+09      青岛中程

[7593 rows x 5 columns]
```
