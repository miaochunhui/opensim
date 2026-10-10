# HTTP Boundary Review

本件核当前独立候选HTTP拒收边界，不是生产、公网或攻击证明。
旧红仅绑定2a86feb；新绿绑定当前完整源码清单与commit前后，
server允许本次独占修复，solver及其余运行依赖仍与2a86feb同SHA。
仅改独立树server.py、本测试和本报告，不复制/chmod/合主/改producer。
测试复用test_server的私有127.0.0.1:0生命周期与workbench.execute/
persist_result证据入口，原始socket只连接本席持有的随机端口。

五方法含原十条控制/负例，新增真实FEM200、Content-Type缺失/空/
重复同值、POST //及畸形HTTP请求行语法首因对照；不运行数值矩阵。
每条绑定当前源码SHA/commit、原始收发字节、状态/error.code与EOF。
null422不称成功FEM；合法CONFIG200另核实际速度/网格/残差/运行身份。
普通unittest discover会独立创建discover-UUID外置证据目录和私有实例，
不skip、不要求only --out，不导入HTTPTests类到本模块导致重复收集。

```bash
sudo -n -u infra python3 -B /data/dev/opensim-resume-sync-chain-20261010/tests/test_http_boundary_review.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/resume-s6-core-precheck-20261010/green-v1
sudo -n -u infra python3 -B /data/dev/opensim-resume-sync-chain-20261010/tests/test_http_boundary_review.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/resume-s6-core-precheck-20261010/server-regression-v1 --regression
```

使用不存在的新输出目录，不覆盖旧失败。实际运行结论和真实launcher rc
由外置summary及execution/终态日志回读，不以本段命令或静态推导代替。
Content-Type重复拒收是无歧义媒体类型策略，不能把接受其中一个值
直接称安全攻击；leading //也只核现有拒收分支，不声称重定向。
资源累计deadline、并发跨用户隔离、代理链及生产行为没有运行验证。

## 实际红证据

raw-v1在2026-10-10 19:32:23至19:32:25 +08:00完成；4方法/10请求，
4失败子例、0异常/跳过，child与真实外层launcher rc均1。
四个基础控制及非空重复TE、plain首值重复Content-Type吻合期望。
空TE、空首值后跟chunked各实得422/INVALID_CONFIG而非400/BODY_LENGTH；
JSON首值重复Content-Type实得422而非415，反序415；
leading //api/health实得200而非400/BAD_PATH。
10条均peer_eof/client_closed=true，listener thread_alive=false/fd=-1。
source-before/after字节相等；当时独立树含仅本两件新增未跟踪文件，
不宣称测试时clean。server/solver五件生产候选源码仍与2a86feb相等。

外置summary：
/hf3fs-jd/data/tmp/opensim-fluid-v1/resume-s6-core-precheck-20261010/summary.md。
原始请求与响应在raw-v1/requests，实际rc在logs/raw-v1-launcher.log。
这确认局部拒收行为，不证明请求走私、公网攻击或任何生产故障。

红后获准独占server.py、本测试、本报告。TE按全部字段presence拒收，
Content-Type唯一且有效；parse_request先捕获raw target，再调用stdlib
保留其解析失败；解析成功才以JSON BAD_PATH拒leading //。
不替换HTTP库，不放宽原合同、上限、Host/Origin或solver验证。
新测试绑定当前candidate完整清单与实际runtime SHA，不追认旧红身份。
旧raw-v1请求/日志/源码清单未覆盖；修后绿与回归各用新目录。

## 修后验收

2026-10-10 19:38:04..19:38:07 +08:00，green-v1 5/5、
普通discover 5/5无skip、server-regression-v1原test_server 14/14，
三轮真实外层及子进程rc0，完整源码清单各自前后相同。
日志中的SOURCE_CHANGED traceback是原HTTP身份漂移负例的预期首因。
运行时独立树HEAD bbae364且仅本三件在制，不称运行时clean。
测试/源码冻结后仅追加本报告；原运行清单仍保留，不追认提交身份。

green-v1十六条raw请求全部EOF；TE空/空首值400 BODY_LENGTH，
CT重复两序/同值415 CONTENT_TYPE，GET/POST //400 BAD_PATH。
畸形请求行400保留stdlib Bad request syntax，非应用BAD_PATH。
合法CONFIG实际200，max_velocity_m_s=0.01000320964885247，
relative_residual=2.6210669201102748e-14；null仍422不算FEM成功。
green及discover私有listener均thread_alive=false/socket_fileno=-1。
新server SHA 668380b300e7f34bab596fa6da52bf2a4d7a6817490517b2748c6b0c86e7b036，
test SHA 1626b934b765a84c2643df770475ff6e899f61101bd0fc3c6df614ad0cf3a0d1。
solver及test_server原件字节未变；主仓仍冻结旧server摘要。
证据：resume-s6-core-precheck-20261010/green-v1、server-regression-v1、
discover-4f3b320bb7284947ad298cc86143bbf3及logs/*-launcher.log。
局部开发自测已绿，提交交S5独立审；不提前合主，不签生产/人验。
