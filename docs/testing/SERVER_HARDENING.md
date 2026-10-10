# Server 安全与缓存验收

## 接口与兼容性

页面生成器输出相对路径 `web/app.<sha256>.js` 和
`web/app.<sha256>.css`。摘要为对应原始文件字节的完整、小写 SHA256，
不是页面构建前缀、mtime 或压缩后摘要。服务仅在请求摘要与读到的内容
精确一致时返回 `public, max-age=31536000, immutable`。
不存在的指纹返回 JSON 404、`no-store`，不重定向或返回新版资源。

`/`、`/index-v1.html`、`/web/app.js`、`/web/app.css` 均保留。
旧资源别名返回 `public, max-age=0, must-revalidate`；
页面、health、计算及错误保持 `no-store`。
HTML 生成器和静态 DOM 验证器使用同一指纹规则。当前公网验证器仍可
读取旧资源别名；浏览器验证器读取页面实际 script URL，不修改验证器。
运行身份集合仍为 CONTRACT 中五份源码，不增加依赖或改变物理范围。

静态资源 MIME 固定为 `text/javascript; charset=utf-8` 与
`text/css; charset=utf-8`。gzip 与 identity 的 ETag 分开；
可压缩响应两种表示均返回 `Vary: Accept-Encoding`。
`If-None-Match` 支持弱比较、列表及 `*`；304 无消息体且省略
Content-Length，保留对应表示的 MIME、ETag、编码及缓存策略。
gzip 使用确定性的时间戳，零权重与非法权重不启用压缩。

## 请求边界

仅接收 origin-form 请求目标，允许 query；拒绝绝对 URL、fragment、
反斜杠、非法百分号编码及控制字符。静态路由仍为允许列表，不解码到
任意文件。要求唯一有效 Host，不引入固定域名允许列表。

POST 可以省略 Origin；有 Origin 时必须唯一，且只含 http/https、
host 和可选 port，不能含路径、query、fragment、userinfo。
按主机大小写归一及 Origin 协议的默认端口比较实际 Host。
HTTPS 可由上游反代终止；反代必须保留外部 Host。
不信任客户端 Forwarded/X-Forwarded-*，也不据其放行跨站请求。

保留既有单计算槽、24 HTTP 槽、15 秒连接超时、16 KB 请求体上限、
重复 Content-Length/Transfer-Encoding 拒绝及严格 JSON 处理。
不吞首因、自动重试或改变超时。运行源码漂移时旧实例仍返回
SOURCE_CHANGED/503；不把磁盘新版本身份赋给旧加载代码。
新版本无法提供旧指纹时返回 404，而非缓存新版内容为旧指纹。

## 复验与证据

在本席工作树用现有入口：

```bash
sudo -n -u infra python3 -B -m unittest discover -s tests -p test_server.py -v
sudo -n -u infra python3 -B -m unittest discover -s tests -p test_page.py -v
sudo -n -u infra python3 -B tools/acceptance.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-server/acceptance-20261010-v2
sudo -n -u infra python3 -B tools/render_page.py --out /3fs/research3fs/data/tmp/opensim-fluid-v1/seat-server/acceptance-20261010-v2/gen --evidence /3fs/research3fs/data/tmp/opensim-fluid-v1/seat-server/acceptance-20261010-v2/numerics.json --check
```

复验使用新验收目录，不覆盖旧报告。acceptance 将外置路径 resolve 为
`/3fs/research3fs/...`；产物正文包含证据来源路径，`--check` 必须使用
产物记录的同一 evidence 参数。改用软链拼写会如实报字节不一致。

HTTP 测试仅启动自身 `127.0.0.1:0` 实例并关闭，核真实响应头、
解压后内容、条件请求、HEAD、资源缓存与实际 FEM 计算。
源码变更测试只修改 seat-server 下独立临时副本，先核旧运行身份 503，
再构造新资源身份核旧指纹 404、新指纹及旧别名对应实际新字节。
元数据测试覆盖完整必需源码、缺件报错、最大 mtime 精确秒级 +08:00、
未变源码页面字节相同及 mtime 不变时内容变更仍更新资源 URL。

修复前失败证据：
`seat-server/baseline-negative-20261010-v1/acceptance.json` 与
`acceptance.log`，2026-10-10 15:44:00 +08:00；
238 测试、30 失败、1 异常，保留畸形 URL 断连和错误放行等首因。
首次复验 `seat-server/acceptance-20261010-v1/` 保留 238 测试中的
1 个测试时序失败：上一 HTTP 线程释放前抢占全部槽位。槽位耗尽检查
改用专属空闲实例，不改变应用上限、超时或引入等待重试。
最终 `seat-server/acceptance-20261010-v2/acceptance.json` 与 log
现读 PASS，2026-10-10 15:48:36 +08:00；238 unittest、5/5 步。
数值 15 算例、3 收敛组、152 拒绝例、170 检查 PASS。
页面 24526 字节、559 检查，重复字节一致；现有产物 `--check` 通过。
页面 SHA256 为
`b05e1a907286207339e63b300366edf99cfc3bb59baed53aafd2bd343870e21d`。
这些本地证据不构成公网同步、完整浏览器或发布门认证。
没有生产变更、push、依赖安装、共享标准修改或代理派生。
