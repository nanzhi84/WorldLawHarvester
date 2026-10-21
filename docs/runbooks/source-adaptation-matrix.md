# 多法域资源适配与推进台账 (Source Adaptation Matrix)

> **唯一事实源与单向生成声明（`CSV -> MD`）**：同目录下的扁平化宽表 [`source-adaptation-matrix.csv`](source-adaptation-matrix.csv)（含全部 574 项 `dd-001`–`dd-574` 条目，拆分为 11 列）是 `WorldLawHarvester` (`wlh`) 所有采集来源身份编号（`dd-xxx`）、国家/法域归属、源级静态元数据（`资料类型`、`来源名称`）及适配推进状态的**唯一事实源（Single Source of Truth）**。本 Markdown 文件由 [`source-adaptation-matrix.csv`](source-adaptation-matrix.csv) 通过文件改动 Hook 调用 [`src/wlh/matrix.py`](../../src/wlh/matrix.py) **单向自动生成**，请勿直接手动修改本文件的表格行。
>
> - 原 [`docs/reference/尽职调查核验入口地图.md`](../reference/尽职调查核验入口地图.md)（`dd-001`–`dd-499`）与 [`docs/reference/公开数据源和开源采集项目调研.md`](../reference/公开数据源和开源采集项目调研.md)（`dd-500`–`dd-574`）作为静态历史调研基线封存不改，其全部条目已 100% 融合至本台账。
> - **新增或变更来源规则**：日常维护直接编辑 [`source-adaptation-matrix.csv`](source-adaptation-matrix.csv)，从当前最大编号（`dd-574`）递增分配 `dd-xxx` ID（严禁重排、回填或复用），配置文件命名为 `sources/dd-xxx.yaml`；保存 CSV 后由同步器自动重算章节统计、生成 `<a id="dd-xxx"></a>dd-xxx` 锚点并更新本文件。

## 登记字段与系统配置映射规则（`资料类型` / `来源名称`）

本台账直接对齐 `sources/dd-xxx.yaml` 配置模型（[`src/wlh/config.py`](../../src/wlh/config.py)）、`canonical` 英文交付投影（[`src/wlh/projections/canonical.py`](../../src/wlh/projections/canonical.py)）及中英字段映射表（[`examples/field_mapping.yaml`](../../examples/field_mapping.yaml)），仅登记源站级静态属性（单篇文档的 `资料名称` 等动态属性在抓取解析时随文提取，不在台账静态列举）：

1. **`资料类型`**（对应 YAML `metadata.document_type` $\rightarrow$ 16 列模板第 3 列 `资料类型`）：
   - **源站级受控枚举**，严格限定为 9 个受控值：`法律法规`、`政策文件`、`案例`、`行业标准`、`通知公告`、`财务报告`、`会计准则`、`税务资料`、`其他`。
   - 新建 `sources/dd-xxx.yaml` 时直接抄入 `metadata.document_type`（若单一 API 含多种类型，如 `dd-499` Federal Register，可在 Adapter 内按单篇记录细化覆盖）。
2. **`来源名称`**（对应 YAML `metadata.source_name` $\rightarrow$ 16 列模板第 14 列 `来源名称`）：
   - **源站级静态标识**，填写实际取得资料或元数据的官方数据库、法院/部委网站或机构平台正式名称（单元格超链接直达官方入口 URL），新建 `sources/dd-xxx.yaml` 时直接抄入 `metadata.source_name`。

---

## 1. 亚洲法域 (Asia)

### 1.1 中国大陆 (China Mainland)

#### 1.1.1 核心官方法律法规、公报与财税库 (`dd-500`–`dd-504`)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-500"></a>dd-500 | 法律法规 | [国家法律法规数据库](https://flk.npc.gov.cn/) | P0 | `web`（接口/浏览器） | [ ] | 普通自动 HTTP 请求返回 403，浏览器可访问，需低频受控会话并核验 DOCX/PDF 下载。 |
| <a id="dd-501"></a>dd-501 | 政策文件 | [中华人民共和国国务院公报](https://www.gov.cn/gongbao/) | P0 | `web` | [ ] | 1954 年至今按年/期号目录发布 HTML 单篇与整期 PDF，可按期次增量。 |
| <a id="dd-502"></a>dd-502 | 法律法规 | [国家规章库](https://www.gov.cn/zhengce/xxgk/gjgzk/index.htm) | P1 | `web` | [ ] | 现行有效部门规章与地方政府规章官方入口，需验证分页枚举完整性。 |
| <a id="dd-503"></a>dd-503 | 法律法规 | [国家行政法规库](https://xzfg.moj.gov.cn/) | P1 | `web` | [ ] | 司法部行政法规正式版本、沿革及文字版/图片版 PDF 单件下载。 |
| <a id="dd-504"></a>dd-504 | 税务资料 | [国家税务总局政策法规库](https://fgk.chinatax.gov.cn/) | P1 | `web` | [ ] | 页面与新近文件可访问，无公开 bulk，按文号与发布日增量巡检（配合 [财政部政策发布](https://www.mof.gov.cn/zhengwuxinxi/zhengcefabu/)）。 |

#### 1.1.2 司法案例、诉讼与仲裁（共 72 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-015"></a>dd-015 | 案例 | [中国裁判文书网](http://wenshu.court.gov.cn/) | 待复查 | 仅人工或授权 | [ ] | 强登录态、动态加密参数与反爬风控，不作为未授权自动采集源。 |
| <a id="dd-016"></a>dd-016 | 案例 | [最高人民法院公报案例](http://gongbao.court.gov.cn/ArticleList.html?serial_no=al) | P2 | `web` | [ ] | 静态列表与详情页结构，待配置 XPath 规则与翻页。 |
| <a id="dd-017"></a>dd-017 | 案例 | [最高人民法院指导案例](https://www.court.gov.cn/shenpan/gengduo/77.html) | P0 | `web`（[`dd-017.yaml`](../../sources/dd-017.yaml)） | [x] | **实网已跑通**：已验证最高人民法院《指导性案例279号》（`490521.html`）`collect` + 离线 `reparse`（`external_requests=0`）。 |
| <a id="dd-018"></a>dd-018 | 案例 | [人民法院案例库](https://rmfyalk.court.gov.cn/) | 暂缓 | `web`（浏览器渲染） | [ ] | 含登录态与接口鉴权限制，需核验公开分页与全文边界。 |
| <a id="dd-019"></a>dd-019 | 通知公告 | [最高人民法院](http://www.court.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-020"></a>dd-020 | 通知公告 | [人民法院公告网](https://rmfygg.court.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-021"></a>dd-021 | 通知公告 | [全国法院被执行人信息查询](http://zxgk.court.gov.cn/zhzxgk/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-022"></a>dd-022 | 通知公告 | [中国执行信息公开网](http://zxgk.court.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-023"></a>dd-023 | 通知公告 | [人民法院诉讼资产网](http://www.rmfysszc.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-024"></a>dd-024 | 通知公告 | [人民法院网上保全系统](https://baoquan.court.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-025"></a>dd-025 | 通知公告 | [中国司法大数据服务网](http://data.court.gov.cn/pages/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-026"></a>dd-026 | 通知公告 | [人民法院司法评估机构名单库](http://zxgk.court.gov.cn/ccpg/findSfpgjgPage) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-027"></a>dd-027 | 通知公告 | [选定评估机构公示](http://zxgk.court.gov.cn/ccpg/index_form_cccz03) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-028"></a>dd-028 | 通知公告 | [人民法院委托鉴定系统](https://dwwtjd.court.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-029"></a>dd-029 | 通知公告 | [人民法院公告网](https://rmfygg.court.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-030"></a>dd-030 | 通知公告 | [人民法院公告自助平台](http://zzpt.rmfyb.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-031"></a>dd-031 | 通知公告 | [人民法院送达平台](http://songda.court.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-032"></a>dd-032 | 案例 | [中国庭审公开网](http://tingshen.court.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-033"></a>dd-033 | 案例 | [全国人民法庭信息网](http://rmft.court.gov.cn/0/index.jhtml) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-034"></a>dd-034 | 案例 | [中国海事审判网](https://cmt.court.gov.cn/hssp-zw/#/index) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-035"></a>dd-035 | 通知公告 | [全国企业破产重整案件信息网](http://pccz.court.gov.cn/pcajxxw/index/xxwsy) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-036"></a>dd-036 | 通知公告 | [人民法院诉讼资产网](http://www.rmfysszc.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-037"></a>dd-037 | 通知公告 | [阿里司法拍卖](https://sf.taobao.com/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-038"></a>dd-038 | 通知公告 | [京东司法拍卖](https://auction.jd.com/sifa.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-039"></a>dd-039 | 政策文件 | [最高人民检察院官网](https://www.spp.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-040"></a>dd-040 | 政策文件 | [最高人民检察院公报](https://www.spp.gov.cn/spp/gjgb/index.shtml) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-041"></a>dd-041 | 案例 | [最高人民检察院指导性案例](https://www.spp.gov.cn/spp/jczdal/index.shtml) | P0 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-042"></a>dd-042 | 案例 | [12309中国检察网](https://www.12309.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-043"></a>dd-043 | 案例 | [12309中国检察网（检察文书公开）](https://login.12309.gov.cn:9443/12309/gj/zjxflws/index.shtml) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-044"></a>dd-044 | 案例 | [中国检察听证网](https://www.12309.gov.cn/jctzw/tzal/listTy.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-045"></a>dd-045 | 通知公告 | [中国仲裁网](http://www.china-arbitration.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-046"></a>dd-046 | 通知公告 | [中国国际经济贸易仲裁委员会](http://www.cietac.org/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-047"></a>dd-047 | 通知公告 | [贸仲立案系统](https://www.cietac.org/articles/15771) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-048"></a>dd-048 | 通知公告 | [北京仲裁委员会](https://www.bjac.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-049"></a>dd-049 | 通知公告 | [北仲网上预约收案](https://yy.bjac.org.cn/bjac/index.html#/pagesGrxx/pc/login/index) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-050"></a>dd-050 | 法律法规 | [中国海事仲裁委员会（仲裁规则）](https://www.cmac.org.cn/index.php?catid=20) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-051"></a>dd-051 | 通知公告 | [天津仲裁委员会](http://www.tjac.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-052"></a>dd-052 | 通知公告 | [郑州仲裁委员会](http://www.zzac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-053"></a>dd-053 | 通知公告 | [青岛仲裁委员会](http://www.qdac.org/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-054"></a>dd-054 | 通知公告 | [济南仲裁委员会](https://zcb.jinan.gov.cn/index.html) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-055"></a>dd-055 | 通知公告 | [太原仲裁委员会](http://www.tyac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-056"></a>dd-056 | 通知公告 | [呼和浩特仲裁委员会](http://huhhotac.org.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-057"></a>dd-057 | 通知公告 | [上海仲裁委员会](http://www.accsh.org/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-058"></a>dd-058 | 通知公告 | [上海国际经济贸易仲裁委员会（上海国际仲裁中心）](http://www.shiac.org/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-059"></a>dd-059 | 通知公告 | [杭州仲裁委员会](http://www.hzhac.org/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-060"></a>dd-060 | 通知公告 | [宁波仲裁委员会](https://www.nbac.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-061"></a>dd-061 | 通知公告 | [南京仲裁委员会](http://ac.nanjing.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-062"></a>dd-062 | 通知公告 | [海南国际仲裁院](http://www.hnac.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-063"></a>dd-063 | 通知公告 | [长沙仲裁委员会](https://www.cscac.net/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-064"></a>dd-064 | 通知公告 | [武汉仲裁委员会](http://www.whac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-065"></a>dd-065 | 通知公告 | [苏州仲裁委员会](http://www.szac.net/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-066"></a>dd-066 | 通知公告 | [大连仲裁委员会](https://www.diac.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-067"></a>dd-067 | 通知公告 | [合肥仲裁委员会](http://www.hfac.net.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-068"></a>dd-068 | 通知公告 | [中国广州仲裁委员会](https://www.gzac.org/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-069"></a>dd-069 | 通知公告 | [深圳国际仲裁院](https://www.scia.com.cn/index.php/Home/index/aboutdetail/id/15.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-070"></a>dd-070 | 通知公告 | [厦门仲裁委员会](http://www.xmac.org.cn/Home) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-071"></a>dd-071 | 通知公告 | [长春仲裁委员会](http://www.ccac.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-072"></a>dd-072 | 通知公告 | [哈尔滨仲裁委员会](http://www.hrbac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-073"></a>dd-073 | 通知公告 | [重庆仲裁委员会](http://www.cqac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-074"></a>dd-074 | 通知公告 | [成都仲裁委员会](http://www.cdac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-075"></a>dd-075 | 通知公告 | [兰州仲裁委员会](http://www.lzzcwyh.com.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-077"></a>dd-077 | 通知公告 | [银川仲裁委员会](http://www.yinchuanzcw.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-078"></a>dd-078 | 通知公告 | [石家庄仲裁委员会](https://sjzac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-079"></a>dd-079 | 通知公告 | [沈阳仲裁委员会](http://syzcb.shenyang.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-080"></a>dd-080 | 通知公告 | [南京仲裁委员会](http://ac.nanjing.gov.cn/zczn/lalct/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-081"></a>dd-081 | 通知公告 | [福州仲裁委员会](http://www.fuzhou.gov.cn/zgfzzt/szcw/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-082"></a>dd-082 | 通知公告 | [南昌仲裁委员会](https://www.nczcw.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-083"></a>dd-083 | 通知公告 | [南宁仲裁委员会](http://www.nnac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-084"></a>dd-084 | 通知公告 | [贵阳仲裁委员会心](https://www.gyac.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-085"></a>dd-085 | 通知公告 | [昆明仲裁委员会](http://kmzcw.km.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-086"></a>dd-086 | 通知公告 | [西安仲裁委员会](http://www.xaac.org/index.php) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-087"></a>dd-087 | 通知公告 | [西宁仲裁委员会](https://china-arbitration.com/index/institution/detail.html?id=3037) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |

#### 1.1.3 行政处罚与信用公示（共 25 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-092"></a>dd-092 | 通知公告 | [信用中国](https://www.creditchina.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-093"></a>dd-093 | 通知公告 | [信用北京](http://creditbj.jxj.beijing.gov.cn/credit-portal/publicity/record/punish_publicity) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-094"></a>dd-094 | 通知公告 | [信用广东](https://credit.gd.gov.cn/) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-095"></a>dd-095 | 通知公告 | [信用山东](http://credit.shandong.gov.cn/creditsearch.publicity.dhtml) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-096"></a>dd-096 | 通知公告 | [信用重庆](http://www.xycq.gov.cn/html/query/punish/list.html?navPage=1) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-097"></a>dd-097 | 通知公告 | [信用四川](https://credit.sc.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-098"></a>dd-098 | 通知公告 | [信用河南](https://credit.henan.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-099"></a>dd-099 | 通知公告 | [中国市场监管行政处罚文书网](http://cfws.samr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-100"></a>dd-100 | 通知公告 | [银保监会/局](https://www.nfra.gov.cn/cn/view/pages/index/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-101"></a>dd-101 | 通知公告 | [司法部（行政处罚与监管信息公开）](https://zwfw.12348.gov.cn/?type=govCallbackTicket&flag=false) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-102"></a>dd-102 | 通知公告 | [外汇局（行政处罚与监管信息公开）](https://www.safe.gov.cn/beijing/xzcfxxgs/index.html) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-103"></a>dd-103 | 通知公告 | [住建部（行政处罚与监管信息公开）](https://www.mohurd.gov.cn/?medium=01) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-104"></a>dd-104 | 通知公告 | [国家统计局（行政处罚与监管信息公开）](https://www.stats.gov.cn/xxgk/list4.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-105"></a>dd-105 | 通知公告 | [海关总署（行政处罚与监管信息公开）](http://www.customs.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-106"></a>dd-106 | 通知公告 | [中国人民银行征信中心](http://www.pbccrc.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-107"></a>dd-107 | 通知公告 | [外商投资企业年度投资经营信息联合报告](http://lhnbgs.mofcom.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-108"></a>dd-108 | 通知公告 | [信息通信行业信用信息查询](https://xy.cace.org.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-109"></a>dd-109 | 通知公告 | [中征应收账款融资服务平台](https://www.crcrfsp.org.cn/index.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-110"></a>dd-110 | 通知公告 | [海事处罚查询](https://zwfw.msa.gov.cn/zwfw/c100021/cxfw.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-111"></a>dd-111 | 通知公告 | [上海市场监督管理局](http://fw.scjgj.sh.gov.cn/shaic/punish!getList.action?firstAccess=1) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-112"></a>dd-112 | 通知公告 | [浙江政务服务网](http://www.zjzwfw.gov.cn/zjzw/punish/frontpunish/showadmins.do?webId=1) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-113"></a>dd-113 | 通知公告 | [深圳市场监督管理局](https://amr.sz.gov.cn/outer/doublePublic/list.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-114"></a>dd-114 | 通知公告 | [成都市监局](https://scjg.chengdu.gov.cn/datacenter/#/double-publicity) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-115"></a>dd-115 | 通知公告 | [武汉市监局](https://scjgj.wuhan.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-116"></a>dd-116 | 通知公告 | [海口市监局](http://amr.haikou.gov.cn/xxgk/spgsxx/xzcfangsxx/) | P0 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |

#### 1.1.4 税务法规、处罚与涉税核验（共 41 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-256"></a>dd-256 | 税务资料 | [纳税人信息查询](https://12366.chinatax.gov.cn/sscx/taxpayer/main) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-257"></a>dd-257 | 税务资料 | [纳税信用查询](https://www.chinatax.gov.cn/chinatax/n810346/c102299/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-258"></a>dd-258 | 税务资料 | [纳税信用A级纳税人名单公布栏](https://www.chinatax.gov.cn/chinatax/n810346/c102299/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-259"></a>dd-259 | 税务资料 | [重大税收违法失信案件信息公布栏](http://www.chinatax.gov.cn/chinatax/c101249/n2020011502/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-260"></a>dd-260 | 税务资料 | [全国增值税发票查验平台](https://inv-veri.chinatax.gov.cn/index.html?saasdianping-221128) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-261"></a>dd-261 | 税务资料 | [信天游](http://www.travelsky.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-262"></a>dd-262 | 税务资料 | [开票信息查询](https://www.qcc.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-263"></a>dd-263 | 税务资料 | [12366查询](https://12366.chinatax.gov.cn/sszyfw/bulletinBoard/main) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-264"></a>dd-264 | 税务资料 | [查询入口2](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jgcx/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-265"></a>dd-265 | 税务资料 | [中国注册税务师协会](http://www.cctaa.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-266"></a>dd-266 | 税务资料 | [税务师事务所查询](http://hy.cctaaedu.cn/hygl/work/hygl/gzcx/swsswsxxcx/index.jsp) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-267"></a>dd-267 | 税务资料 | [注册税务师查询](http://hy.cctaaedu.cn/hygl/work/hygl/gzcx/zcswsxxcx/index.jsp) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-268"></a>dd-268 | 税务资料 | [国家税务总局北京税务局（涉税行政处罚公示）](http://beijing.chinatax.gov.cn/bjsat/xzzf/xzzf_sj/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-269"></a>dd-269 | 税务资料 | [国家税务总局上海税务局（涉税行政处罚公示）](http://shanghai.chinatax.gov.cn/newxbwz/wzcx/QYDA_qyda03_xzcfajxxcx.jsp?swjgDm=13100000000) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-270"></a>dd-270 | 税务资料 | [国家税务总局浙江税务局（涉税行政处罚公示）](http://zhejiang.chinatax.gov.cn:8080/sword?ctrl=XzzfgsptHomeCtrl_index&yjcd=xzcf&ejcd=xzcf_01) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-271"></a>dd-271 | 税务资料 | [国家税务总局江苏税务局（涉税行政处罚公示）](https://jiangsu.chinatax.gov.cn/fggspt/main.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-272"></a>dd-272 | 税务资料 | [国家税务总局江苏2税务局（涉税行政处罚公示）](https://jiangsu.chinatax.gov.cn/col/col8407/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-273"></a>dd-273 | 税务资料 | [国家税务总局山东税务局（涉税行政处罚公示）](http://shandong.chinatax.gov.cn/zfgspt/extranet/index#1) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-274"></a>dd-274 | 税务资料 | [国家税务总局河南税务局（涉税行政处罚公示）](https://henan.chinatax.gov.cn/xxgspt/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-275"></a>dd-275 | 税务资料 | [国家税务总局湖北税务局（涉税行政处罚公示）](http://hubei.chinatax.gov.cn/gspt/jingmen/dbq/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-276"></a>dd-276 | 税务资料 | [国家税务总局湖南税务局（涉税行政处罚公示）](http://hunan.chinatax.gov.cn/category/20200325400506) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-277"></a>dd-277 | 税务资料 | [国家税务总局广东税务局（涉税行政处罚公示）](http://guangdong.chinatax.gov.cn/gdzfgs/zfpt_index.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-278"></a>dd-278 | 税务资料 | [国家税务总局广西税务局（涉税行政处罚公示）](http://113.12.192.120:8208/zfgspt/extranet/xzzs) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-279"></a>dd-279 | 税务资料 | [国家税务总局四川税务局（涉税行政处罚公示）](https://etax.sichuan.chinatax.gov.cn/zfgspt/extranet/index#1) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-280"></a>dd-280 | 税务资料 | [国家税务总局贵州税务局（涉税行政处罚公示）](http://guizhou.chinatax.gov.cn/xzzfxxgspts/gzsswj/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-281"></a>dd-281 | 税务资料 | [国家税务总局陕西税务局（涉税行政处罚公示）](http://zfgs.shaanxi.chinatax.gov.cn:88/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-282"></a>dd-282 | 税务资料 | [国家税务总局甘肃税务局（涉税行政处罚公示）](http://gansu.chinatax.gov.cn/col/col49/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-283"></a>dd-283 | 税务资料 | [国家税务总局青海税务局（涉税行政处罚公示）](http://etax.qinghai.chinatax.gov.cn:7201/sword?ctrl=XzzfgsptHomeCtrl_index) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-284"></a>dd-284 | 税务资料 | [国家税务总局宁夏税务局（涉税行政处罚公示）](https://zfgs.ningxia.chinatax.gov.cn:9443/sword?ctrl=XzzfgsptHomeCtrl_index) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-285"></a>dd-285 | 税务资料 | [国家税务总局内蒙古税务局（涉税行政处罚公示）](https://zfxxgs.neimenggu.chinatax.gov.cn/sword?ctrl=XzzfgsptHomeCtrl_index) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-286"></a>dd-286 | 税务资料 | [国家税务总局山西税务局（涉税行政处罚公示）](http://shanxi.chinatax.gov.cn/xzzfgsptWeb/web) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-287"></a>dd-287 | 税务资料 | [国家税务总局河北税务局（涉税行政处罚公示）](http://wzyy.hebei.chinatax.gov.cn/LawPublicity/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-288"></a>dd-288 | 税务资料 | [国家税务总局天津税务局（涉税行政处罚公示）](http://tianjin.chinatax.gov.cn/xzzfgspt/init_xzzfmain.action) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-289"></a>dd-289 | 税务资料 | [国家税务总局西藏税务局（涉税行政处罚公示）](https://xizang.chinatax.gov.cn/col/col7790/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-290"></a>dd-290 | 税务资料 | [国家税务总局新疆税务局（涉税行政处罚公示）](https://etax.xinjiang.chinatax.gov.cn:16001/zfgspt/extranet/jcxx) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-291"></a>dd-291 | 税务资料 | [国家税务总局12366纳税服务平台](https://12366.chinatax.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-292"></a>dd-292 | 税务资料 | [税收政策库](http://www.chinatax.gov.cn/chinatax/n810341/n810825/index.html?title=) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-293"></a>dd-293 | 税务资料 | [进出口商品税率查询](http://202.127.48.116:18001/static/pages/taxRateQuery.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-294"></a>dd-294 | 税务资料 | [出口退税率查询](https://hd.chinatax.gov.cn/nszx2023/cktslcx2023.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-295"></a>dd-295 | 税务资料 | [商品和服务税收分类编码表](http://www.chinatax.gov.cn/n810341/n810755/c2978671/content.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-296"></a>dd-296 | 税务资料 | [进出口税目税号查询](http://www.customs.gov.cn/customs/302427/302442/jckszcx/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |

#### 1.1.5 市场主体登记与公示系统（共 6 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-001"></a>dd-001 | 其他 | [企业信用信息公示](http://www.gsxt.gov.cn/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-002"></a>dd-002 | 其他 | [北京市企业信用信息网](http://qyxy.scjgj.beijing.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-003"></a>dd-003 | 其他 | [天津市市场主体信用信息公示系统](http://credit.scjg.tj.gov.cn/gsxt/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-004"></a>dd-004 | 其他 | [深圳商事主体信用监管公示平台](https://amr.sz.gov.cn/xyjggs.webui/xyjggs/index.aspx) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-005"></a>dd-005 | 其他 | [东莞市企业信用信息公示系统](http://qyxy.dg.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-006"></a>dd-006 | 其他 | [厦门市商事主体登记及信用信息公示平台](https://credit.scjg.xm.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |

#### 1.1.6 知识产权、不动产与动产登记（共 21 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-117"></a>dd-117 | 其他 | [中登网（动产融资统一登记公示系统）](https://www.zhongdengwang.org.cn/) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-118"></a>dd-118 | 其他 | [全国不动产登记网上查询](https://bdcyc.mnr.gov.cn/#/index/adminArea.html?type=CXURL) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-119"></a>dd-119 | 通知公告 | [北京市不动产登记中心（不动产登记公告）](http://yewu.ghzrzyw.beijing.gov.cn/gwxxfb/bdcdjgggs/bdcdjgggs.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-120"></a>dd-120 | 通知公告 | [深圳市不动产登记中心（不动产登记公告）](http://pnr.sz.gov.cn/ywzy/bdcdj/szbdc/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-121"></a>dd-121 | 通知公告 | [广州市不动产登记中心（不动产登记公告）](https://ghzyj.gz.gov.cn/zwfw/xtzq/gzsbdcdjzlly/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-122"></a>dd-122 | 通知公告 | [珠海市不动产登记中心（不动产登记公告）](http://bdc.zhuhai.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-123"></a>dd-123 | 通知公告 | [东莞市不动产登记中心（不动产登记公告）](https://nr.dg.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-124"></a>dd-124 | 通知公告 | [上海市不动产登记中心（不动产登记公告）](https://ghzyj.sh.gov.cn/bdcdj/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-125"></a>dd-125 | 其他 | [中国专利检索及分析](https://pss-system.cponline.cnipa.gov.cn/Disclaime) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-126"></a>dd-126 | 其他 | [中国及多国专利审查信息查询](https://cpquery.cponline.cnipa.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-127"></a>dd-127 | 其他 | [专利复审和无效查询](https://tysf.cponline.cnipa.gov.cn/am/#/user/login) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-128"></a>dd-128 | 其他 | [专利代理机构查询](http://dlgl.cnipa.gov.cn/txnqueryAgencyOrg.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-129"></a>dd-129 | 其他 | [专利代理师信息查询](http://dlgl.cnipa.gov.cn/txnqueryAgent.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-133"></a>dd-133 | 其他 | [中国专利保护协会](http://www.ppac.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-134"></a>dd-134 | 其他 | [全国专利代理公共服务平台](http://www.pppas.net/index) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-135"></a>dd-135 | 其他 | [北京知识产权公共信息服务平台](http://search.beijingip.cn/search/search/index) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-136"></a>dd-136 | 其他 | [上海市知识产权专利检索分析平台](http://xxfw.sipa.sh.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-138"></a>dd-138 | 其他 | [专利检索及分析系统](https://pss-system.cponline.cnipa.gov.cn/conventionalSearch) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-147"></a>dd-147 | 其他 | [中国商标网](http://wcjs.sbj.cnipa.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-152"></a>dd-152 | 其他 | [工信部ICP/IP地址/域名信息备案管理系统](https://beian.miit.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-153"></a>dd-153 | 其他 | [中国互联网信息中心](http://www.cnnic.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |

#### 1.1.7 资本市场、证券基金与产权交易披露（共 96 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-154"></a>dd-154 | 通知公告 | [证监会](http://www.csrc.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-155"></a>dd-155 | 通知公告 | [证监会统计信息](http://www.csrc.gov.cn/csrc/tjsj/index.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-156"></a>dd-156 | 政策文件 | [证监会业务办理指南](https://neris.csrc.gov.cn/alappl/home/guideH) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-157"></a>dd-157 | 财务报告 | [巨潮网](http://www.cninfo.com.cn/new/index) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-158"></a>dd-158 | 财务报告 | [深交所](http://www.szse.cn/disclosure/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-159"></a>dd-159 | 财务报告 | [上交所](https://www.sse.com.cn/?c=index) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-160"></a>dd-160 | 财务报告 | [上交所科创板](http://star.sse.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-163"></a>dd-163 | 财务报告 | [北交所](http://www.bse.cn/disclosure/announcement.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-168"></a>dd-168 | 通知公告 | [挂牌公司公告](http://www.neeq.com.cn/disclosure/overview_information.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-169"></a>dd-169 | 通知公告 | [两网及退市公司公告](http://www.neeq.com.cn/disclosure/snadc_announcement.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-170"></a>dd-170 | 政策文件 | [新三板规则](http://www.neeq.com.cn/rule/list.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-171"></a>dd-171 | 通知公告 | [挂牌审查](http://www.neeq.com.cn/disclosure/listing_review.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-172"></a>dd-172 | 通知公告 | [定向发行审查](http://www.neeq.com.cn/disclosure/review_open_information/stock_issue_review.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-173"></a>dd-173 | 通知公告 | [重大资产重组审查](http://www.neeq.com.cn/disclosure/major_asset_restructuring.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-174"></a>dd-174 | 通知公告 | [全国股转系统监管公告](http://www.neeq.com.cn/disclosure/supervise.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-175"></a>dd-175 | 通知公告 | [[证监会]非上市公众公司行政许可](http://www.csrc.gov.cn/csrc/c101830/c1021140/content.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-176"></a>dd-176 | 通知公告 | [[证监会]公众公司定向发行批复](http://www.csrc.gov.cn/csrc/c100095/common_list.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-177"></a>dd-177 | 通知公告 | [中国债券信息网](http://www.chinabond.com.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-178"></a>dd-178 | 通知公告 | [中国银行间市场交易商协会](http://nafmii.org.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-179"></a>dd-179 | 通知公告 | [全国银行间同业拆借中心](http://www.chinamoney.com.cn/chinese/zqfx/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-180"></a>dd-180 | 通知公告 | [上交所债券](http://www.sse.com.cn/disclosure/bond/announcement/bookentry/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-181"></a>dd-181 | 通知公告 | [深交所债券](http://www.szse.cn/disclosure/bond/notice/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-182"></a>dd-182 | 通知公告 | [巨潮债券](http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice#bond) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-183"></a>dd-183 | 政策文件 | [地方债管理](http://yss.mof.gov.cn/zhuantilanmu/dfzgl/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-184"></a>dd-184 | 通知公告 | [中国地方政府债券信息公开平台](http://www.celma.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-185"></a>dd-185 | 通知公告 | [债券监管司公开发行公司债券反馈意见](http://www.csrc.gov.cn/csrc/c106307/c1045428/content.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-186"></a>dd-186 | 通知公告 | [[证监会]区域性股权市场](http://www.csrc.gov.cn/pub/newsite/djffzqqhhdj/qyxgqzrsc/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-187"></a>dd-187 | 通知公告 | [北京区域性股权市场](https://www.bjotc.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-188"></a>dd-188 | 通知公告 | [上海股权托管交易中心](https://www.china-see.com/index.do) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-189"></a>dd-189 | 通知公告 | [中原股权交易中心](https://www.ccotc.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-190"></a>dd-190 | 通知公告 | [辽宁股权交易中心](http://service.lneec.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-191"></a>dd-191 | 通知公告 | [山西股权交易中心](https://www.sxgq.net/m/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-192"></a>dd-192 | 通知公告 | [陕西股权交易中心](http://www.sxeec.com/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-193"></a>dd-193 | 通知公告 | [青海股权交易中心](http://www.qheec.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-194"></a>dd-194 | 通知公告 | [甘肃股权交易中心](http://www.gsotc.com.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-195"></a>dd-195 | 通知公告 | [新疆股权交易中心](http://www.casdaq.com.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-196"></a>dd-196 | 通知公告 | [江苏股权交易中心](http://www.jseec.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-197"></a>dd-197 | 通知公告 | [全国产权行业信息化综合服务平台](https://www.cspea.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-198"></a>dd-198 | 通知公告 | [中国企业国有产权交易机构协会](http://www.cspea.org.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-199"></a>dd-199 | 通知公告 | [北京产权交易所](https://www.cbex.com.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-200"></a>dd-200 | 通知公告 | [北京金融资产交易所](https://www.cfae.cn/index/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-201"></a>dd-201 | 通知公告 | [天津产权交易中心](https://www.tpre.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-202"></a>dd-202 | 通知公告 | [天津农村产权交易所](http://www.tjnjs.com/#/home/home) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-203"></a>dd-203 | 通知公告 | [河北产权市场](http://www.hebaee.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-204"></a>dd-204 | 通知公告 | [上海联合产权交易所](https://www.suaee.com/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-205"></a>dd-205 | 通知公告 | [杭州产权交易所](http://www.hzaee.com/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-206"></a>dd-206 | 通知公告 | [宁波产权交易中心](http://www.nbcqjy.org/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-207"></a>dd-207 | 通知公告 | [南方联合产权交易中心](https://www.csuaee.com.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-208"></a>dd-208 | 通知公告 | [广东联合产权交易中心](https://www.gduaee.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-209"></a>dd-209 | 通知公告 | [广州产权交易所](http://gz.gemas.com.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-210"></a>dd-210 | 通知公告 | [深圳联合产权交易所](http://www.sotcbb.com/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-211"></a>dd-211 | 通知公告 | [珠海产权交易中心](http://www.zhaeec.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-212"></a>dd-212 | 通知公告 | [佛山南方产权交易所](https://www.fsaee.com/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-213"></a>dd-213 | 通知公告 | [广西联合产权交易所](http://www.gxcq.com.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-214"></a>dd-214 | 通知公告 | [广西北部湾产权交易所](http://www.bbwcq.com/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-215"></a>dd-215 | 通知公告 | [福建省产权交易所](https://www.fjcqjy.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-216"></a>dd-216 | 通知公告 | [厦门产权交易中心](http://www.xemas.com.cn/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-217"></a>dd-217 | 通知公告 | [泉州市产权交易中心](http://www.qzcq0595.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-218"></a>dd-218 | 通知公告 | [江苏产权交易所](http://www.jscq.com.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-219"></a>dd-219 | 通知公告 | [无锡产权交易所](https://www.wxcq.com.cn/wuxicq/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-220"></a>dd-220 | 通知公告 | [常州产权交易所](http://www.czcq.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-221"></a>dd-221 | 通知公告 | [西南联合产权交易所](https://www.swuee.com/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-222"></a>dd-222 | 通知公告 | [云南产权交易所](http://www.cynee.net/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-223"></a>dd-223 | 通知公告 | [贵州阳光产权交易所](http://www.prechina.net/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-224"></a>dd-224 | 通知公告 | [甘肃省产权交易所](http://www.gscq.com.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-225"></a>dd-225 | 通知公告 | [江西省产权交易所](https://jxcq.jxggzyjy.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-226"></a>dd-226 | 通知公告 | [湖南联合产权交易所](http://www.hnaee.com/hnaee/index.htm) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-227"></a>dd-227 | 通知公告 | [长沙联合产权交易所](https://www.cscqjy.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-228"></a>dd-228 | 通知公告 | [重庆产权交易网](https://cquae.cqggzy.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-229"></a>dd-229 | 通知公告 | [黑龙江联合产权交易所](http://www.hljcqjy.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-230"></a>dd-230 | 通知公告 | [河南中原产权交易有限公司](http://www.zycqjy.com/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-231"></a>dd-231 | 通知公告 | [郑州市公共资源交易中心](https://zzggzy.zhengzhou.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-232"></a>dd-232 | 通知公告 | [安徽产权交易中心](http://www.aaee.com.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-233"></a>dd-233 | 通知公告 | [安徽省农村综合产权交易网](http://www.ahaee.com/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-234"></a>dd-234 | 通知公告 | [安徽长江产权交易所](http://www.ccjex.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-235"></a>dd-235 | 通知公告 | [山东产权交易中心](http://www.sdcqjy.com/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-236"></a>dd-236 | 通知公告 | [青岛产权交易所](http://www.qdcq.net/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-237"></a>dd-237 | 通知公告 | [烟台联合产权交易中心](http://www.ytcq.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-238"></a>dd-238 | 通知公告 | [潍坊产权交易中心](http://www.wfcqjy.com/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-239"></a>dd-239 | 通知公告 | [山西省产权交易市场](https://www.sxcqjy.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-240"></a>dd-240 | 通知公告 | [长治产权交易市场](http://www.czscq.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-241"></a>dd-241 | 通知公告 | [哈尔滨产权交易中心](https://hrbggzy.harbin.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-242"></a>dd-242 | 通知公告 | [宁夏科技资源与产权交易所](https://www.ntree.com.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-243"></a>dd-243 | 通知公告 | [新疆产权交易市场](http://www.xjcq.net.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-244"></a>dd-244 | 通知公告 | [海南产权交易所](http://www.hncq.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-245"></a>dd-245 | 通知公告 | [证券投资基金业协会信息公示系统](http://gs.amac.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-246"></a>dd-246 | 通知公告 | [中国证券投资基金业协会](https://www.amac.org.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-247"></a>dd-247 | 通知公告 | [公募基金管理人](https://www.amac.org.cn/fwdt/wyc/jgcprycx/jgcx/gmjjglrml/) | P1 | `web`（参数/接口） | [ ] | 公开参数查询或 API 入口，可按固定查询参数归档。 |
| <a id="dd-248"></a>dd-248 | 通知公告 | [公募基金销售机构](https://www.amac.org.cn/fwdt/wyc/jgcprycx/jgcx/gmjjxsjggs/) | P1 | `web`（参数/接口） | [ ] | 公开参数查询或 API 入口，可按固定查询参数归档。 |
| <a id="dd-249"></a>dd-249 | 通知公告 | [私募基金管理人](http://gs.amac.org.cn/amac-infodisc/res/pof/manager/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-250"></a>dd-250 | 通知公告 | [基金托管机构](https://www.amac.org.cn/fwdt/wyc/jgcprycx/jgcx/jjtgrjjgs/) | P1 | `web`（参数/接口） | [ ] | 公开参数查询或 API 入口，可按固定查询参数归档。 |
| <a id="dd-251"></a>dd-251 | 通知公告 | [私募基金](http://gs.amac.org.cn/amac-infodisc/res/pof/fund/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-252"></a>dd-252 | 通知公告 | [证券公司直投基金](http://gs.amac.org.cn/amac-infodisc/res/aoin/product/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-253"></a>dd-253 | 通知公告 | [证券公司私募投资基金](http://gs.amac.org.cn/amac-infodisc/res/pof/subfund/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-254"></a>dd-254 | 通知公告 | [基金公司及子公司集合资管](http://gs.amac.org.cn/amac-infodisc/res/fund/account/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-255"></a>dd-255 | 通知公告 | [基金从业人员](https://gs.amac.org.cn/amac-infodisc/res/pof/person/personOrgList.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |

#### 1.1.8 自然资源、土地矿产与生态环境公示（共 78 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-297"></a>dd-297 | 通知公告 | [自然资源部数据服务](http://www.mnr.gov.cn/sj/sjfw/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-298"></a>dd-298 | 通知公告 | [自然资源部行政审批信息公开](https://www.mnr.gov.cn/zt/zh/xzzfgs/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-299"></a>dd-299 | 通知公告 | [自然资源确权登记网](https://www.rerc.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-300"></a>dd-300 | 通知公告 | [中国土地市场网](https://www.landchina.com/#/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-301"></a>dd-301 | 通知公告 | [全国征地信息共享平台](http://z.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-302"></a>dd-302 | 通知公告 | [全国土地估价监管系统](http://tdgj.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-303"></a>dd-303 | 通知公告 | [土地估价行业信息公示系统](http://gujia.mnr.gov.cn/JGPublish/Publish/Index) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-304"></a>dd-304 | 通知公告 | [全国主要城市地价监测报告](http://www.mnr.gov.cn/sj/sjfw/td_19262/djjcbg/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-305"></a>dd-305 | 通知公告 | [土地利用现状分类](http://c.gb688.cn/bzgk/gb/showGb?type=online&hcno=224BF9DA69F053DA22AC758AAAADEEAA) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-306"></a>dd-306 | 通知公告 | [矿业权市场](http://ky.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-307"></a>dd-307 | 通知公告 | [全国矿业权人勘查开采信息公示系统](http://kyqgs.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-308"></a>dd-308 | 通知公告 | [全球地质矿产信息系统](http://geoglobal.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-309"></a>dd-309 | 通知公告 | [全国地质资料馆](http://www.ngac.cn/125cms/c/qggnew/index.htm) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-310"></a>dd-310 | 通知公告 | [全国矿产地数据库](http://geochina.cgs.gov.cn/article/doi/10.12029/gc2019Z201) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-311"></a>dd-311 | 通知公告 | [地质云](https://geocloud.cgs.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-312"></a>dd-312 | 通知公告 | [境外矿产资源开发备案](http://kczy.mofcom.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-313"></a>dd-313 | 通知公告 | [林木种子生产经营许可证查询](https://ydbg.forestry.gov.cn/adminapproval/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-314"></a>dd-314 | 通知公告 | [林木业和草原行政许可查询](https://ydbg.forestry.gov.cn/adminapproval/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-315"></a>dd-315 | 通知公告 | [珍稀动物查询](https://endangeredanimal.foodmate.net/index?type=1&cid=49) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-316"></a>dd-316 | 通知公告 | [林业和草原局行政许可查询](https://ydbg.forestry.gov.cn/adminapproval/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-317"></a>dd-317 | 通知公告 | [北京自然资源厅/局（政务与审批公开）](http://ghzrzyw.beijing.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-318"></a>dd-318 | 通知公告 | [天津自然资源厅/局（政务与审批公开）](http://ghhzrzy.tj.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-319"></a>dd-319 | 通知公告 | [河北自然资源厅/局（政务与审批公开）](http://zrzy.hebei.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-320"></a>dd-320 | 通知公告 | [山西自然资源厅/局（政务与审批公开）](http://zrzyt.shanxi.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-321"></a>dd-321 | 通知公告 | [内蒙古自然资源厅/局（政务与审批公开）](http://zrzy.nmg.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-322"></a>dd-322 | 通知公告 | [辽宁自然资源厅/局（政务与审批公开）](http://zrzy.ln.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-323"></a>dd-323 | 通知公告 | [吉林自然资源厅/局（政务与审批公开）](http://zrzy.jl.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-324"></a>dd-324 | 通知公告 | [黑龙江自然资源厅/局（政务与审批公开）](http://zrzyt.hlj.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-325"></a>dd-325 | 通知公告 | [上海自然资源厅/局（政务与审批公开）](http://ghzyj.sh.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-326"></a>dd-326 | 通知公告 | [江苏自然资源厅/局（政务与审批公开）](http://zrzy.jiangsu.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-327"></a>dd-327 | 通知公告 | [浙江自然资源厅/局（政务与审批公开）](http://zrzyt.zj.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-328"></a>dd-328 | 通知公告 | [安徽自然资源厅/局（政务与审批公开）](http://zrzyt.ah.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-329"></a>dd-329 | 通知公告 | [福建自然资源厅/局（政务与审批公开）](http://zrzyt.fujian.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-330"></a>dd-330 | 通知公告 | [江西自然资源厅/局（政务与审批公开）](http://bnr.jiangxi.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-331"></a>dd-331 | 通知公告 | [山东自然资源厅/局（政务与审批公开）](http://dnr.shandong.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-332"></a>dd-332 | 通知公告 | [河南自然资源厅/局（政务与审批公开）](http://dnr.henan.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-333"></a>dd-333 | 通知公告 | [湖北自然资源厅/局（政务与审批公开）](http://zrzyt.hubei.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-334"></a>dd-334 | 通知公告 | [湖南自然资源厅/局（政务与审批公开）](http://zrzyt.hunan.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-335"></a>dd-335 | 通知公告 | [广东自然资源厅/局（政务与审批公开）](http://nr.gd.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-336"></a>dd-336 | 通知公告 | [广西自然资源厅/局（政务与审批公开）](http://dnr.gxzf.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-337"></a>dd-337 | 通知公告 | [海南自然资源厅/局（政务与审批公开）](http://lr.hainan.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-338"></a>dd-338 | 通知公告 | [重庆自然资源厅/局（政务与审批公开）](http://ghzrzyj.cq.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-339"></a>dd-339 | 通知公告 | [四川自然资源厅/局（政务与审批公开）](http://dnr.sc.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-340"></a>dd-340 | 通知公告 | [贵州自然资源厅/局（政务与审批公开）](http://zrzy.guizhou.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-341"></a>dd-341 | 通知公告 | [云南自然资源厅/局（政务与审批公开）](http://dnr.yn.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-342"></a>dd-342 | 通知公告 | [陕西自然资源厅/局（政务与审批公开）](http://zrzyt.shaanxi.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-343"></a>dd-343 | 通知公告 | [甘肃自然资源厅/局（政务与审批公开）](http://zrzy.gansu.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-344"></a>dd-344 | 通知公告 | [青海自然资源厅/局（政务与审批公开）](http://zrzyt.qinghai.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-345"></a>dd-345 | 通知公告 | [宁夏自然资源厅/局（政务与审批公开）](http://zrzyt.nx.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-346"></a>dd-346 | 通知公告 | [新疆自然资源厅/局（政务与审批公开）](http://zrzyt.xinjiang.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-347"></a>dd-347 | 通知公告 | [大连自然资源厅/局（政务与审批公开）](http://www.zrzy.dl.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-348"></a>dd-348 | 通知公告 | [青岛自然资源厅/局（政务与审批公开）](http://zrzygh.qingdao.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-349"></a>dd-349 | 通知公告 | [宁波自然资源厅/局（政务与审批公开）](http://zgj.ningbo.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-350"></a>dd-350 | 通知公告 | [厦门自然资源厅/局（政务与审批公开）](http://zygh.xm.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-351"></a>dd-351 | 通知公告 | [深圳自然资源厅/局（政务与审批公开）](http://pnr.sz.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-352"></a>dd-352 | 通知公告 | [兵团自然资源厅/局（政务与审批公开）](http://zrzy.xjbt.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-353"></a>dd-353 | 通知公告 | [国家地理信息公共服务平台](https://www.tianditu.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-354"></a>dd-354 | 通知公告 | [自然资源部测绘地理信息政务服务](http://zwfw.ch.mnr.gov.cn/index;jsessionid=211B19F02ED92247C3EC00D86051DA46) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-355"></a>dd-355 | 通知公告 | [自然资源部科技成果查询数据库](http://jlps.mnr.gov.cn/global/reward!KJCGsearch.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-356"></a>dd-356 | 通知公告 | [自然资源部获奖成果查询](http://jlps.mnr.gov.cn/global/reward!listAllReward.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-357"></a>dd-357 | 通知公告 | [全国排污许可证管理信息平台](http://permit.mee.gov.cn/permitExt/defaults/default-index!getInformation.action) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-358"></a>dd-358 | 通知公告 | [环境影响评价信用平台](http://114.251.10.92:8080/XYPT/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-359"></a>dd-359 | 通知公告 | [生态环境部污染源监控中心平台](http://www.envsc.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-360"></a>dd-360 | 政策文件 | [环境保护综合目录](http://www.gov.cn/xinwen/2018-02/06/5264316/files/27c5704a32e941e8ac20e40d61209a94.pdf) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-361"></a>dd-361 | 通知公告 | [全国环境影响评价管理信息平台](https://eia.lem.org.cn/#/eia) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-362"></a>dd-362 | 通知公告 | [环境影响评价信息公示平台](http://www.js-eia.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-363"></a>dd-363 | 通知公告 | [建设项目环境影响登记表查询&gt;&gt;](https://beian.china-eia.com/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-364"></a>dd-364 | 通知公告 | [取水许可审批信息查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/qsxkxxcx/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-365"></a>dd-365 | 通知公告 | [水利工程建设监理单位资质查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/sljlzzcx/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-366"></a>dd-366 | 通知公告 | [水利工程质量检测单位资质查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/slzlzzcx/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-367"></a>dd-367 | 通知公告 | [中国安全产业协会](http://www.chinasia.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-368"></a>dd-368 | 通知公告 | [中国安全生产协会](http://www.china-safety.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-369"></a>dd-369 | 通知公告 | [中国再生资源回收利用协会](http://www.crra.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-370"></a>dd-370 | 通知公告 | [中国化学品安全协会](http://www.chemicalsafety.org.cn/index.php) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-371"></a>dd-371 | 通知公告 | [中国水利工程协会](http://www.cweun.org/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-372"></a>dd-372 | 通知公告 | [中国水利企业协会](http://www.cwec.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-373"></a>dd-373 | 通知公告 | [中国环境保护产业协会](http://www.caepi.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-374"></a>dd-374 | 通知公告 | [中华环保联合会](http://www.acef.com.cn/index.html) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |

#### 1.1.9 行业标准与特定行业资质监管（共 107 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-375"></a>dd-375 | 行业标准 | [国民经济行业分类](https://www.stats.gov.cn/xxgk/tjbz/gjtjbz/201905/t20190521_1758938.html) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-376"></a>dd-376 | 行业标准 | [[证监会]上市公司行业分类指引](http://www.csrc.gov.cn/csrc/c100103/c1452025/content.shtml) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-377"></a>dd-377 | 行业标准 | [新三板行业分类指引](http://www.neeq.com.cn/m/fenglei/hyfl01.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-378"></a>dd-378 | 行业标准 | [战略性新兴产业分类](https://www.stats.gov.cn/xxgk/tjbz/gjtjbz/202008/t20200811_1782344.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-379"></a>dd-379 | 通知公告 | [工信部电信业务市场综合管理信息系统](https://tsm.miit.gov.cn/dxxzsp/) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-380"></a>dd-380 | 通知公告 | [高新技术企业认定管理工作网](http://www.innocom.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-381"></a>dd-381 | 通知公告 | [高新技术企业备案查询](https://fuwu.most.gov.cn/html/fwcx/gxjsqybacx/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-382"></a>dd-382 | 通知公告 | [国家科学技术奖励查询1](http://www.most.gov.cn/cxfw/kjjlcx/index.htm) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-383"></a>dd-383 | 通知公告 | [国家科学技术奖励查询2](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/kjjl/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-384"></a>dd-384 | 通知公告 | [国家级科技企业孵化器查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/fhq/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-385"></a>dd-385 | 通知公告 | [国家级高新技术产业开发区名单](http://www.chinatorch.gov.cn/gxq/gxqmd/list.shtml) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-386"></a>dd-386 | 通知公告 | [众创空间查询](https://fuwu.most.gov.cn/html/fwcx/zckjcx/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-387"></a>dd-387 | 通知公告 | [电信设备进网许可标志查询](https://jwxkjwgl.miit.gov.cn/gatewayauthenticityquery) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-388"></a>dd-388 | 通知公告 | [移动电话机图片查询](https://jwxkjwgl.miit.gov.cn/gatewayMobilePictureQuery) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-389"></a>dd-389 | 通知公告 | [国家保密科技测评中心](http://www.nsstec.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-390"></a>dd-390 | 通知公告 | [中国汽车燃料消耗量查询系统](https://yhgscx.miit.gov.cn/fuel-consumption-web/mainPage) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-391"></a>dd-391 | 通知公告 | [新能源汽车动力蓄电池回收服务网点信息](https://www.miit.gov.cn/datainfo/zysjk/xnyqcdlxdchsfwwdxx/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-392"></a>dd-392 | 通知公告 | [电器电子产品有害物质限制使用公共服务平台](https://chinarohs.miit.gov.cn/#/index) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-393"></a>dd-393 | 通知公告 | [电子认证服务使用密码许可单位名录](http://www.oscca.gov.cn/app-zxfw/xzspsx/syxkdw.jsp?channel_code=c100142) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-394"></a>dd-394 | 通知公告 | [外商投资电信企业设立审批](https://www.miit.gov.cn/datainfo/zzsjk/wstzdxqyslsp/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-395"></a>dd-395 | 通知公告 | [互联网电子公告服务审批及备案](https://www.miit.gov.cn/datainfo/zzsjk/hlwdzggfwzxspjba/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-396"></a>dd-396 | 通知公告 | [全国互联网安全管理服务平台](http://www.beian.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-397"></a>dd-397 | 通知公告 | [军工电子计量技术机构建立认定](https://www.miit.gov.cn/datainfo/zzsjk/jgdzjljsjgjlrd/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-398"></a>dd-398 | 通知公告 | [互联网信息服务投诉平台](https://ts.isc.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-399"></a>dd-399 | 通知公告 | [中国临床试验注册中心检索入口](https://www.chictr.org.cn/searchproj.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-400"></a>dd-400 | 通知公告 | [药物临床试验登记与信息公示平台](http://www.chinadrugtrials.org.cn/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-401"></a>dd-401 | 通知公告 | [国家自然科学基金项目综合查询](https://kd.nsfc.cn/fundingProjectInit) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-402"></a>dd-402 | 通知公告 | [【科技部】人类遗传资源管理审批](https://fuwu.most.gov.cn/html/jgcx/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-403"></a>dd-403 | 通知公告 | [国家药监局数据查询](https://www.nmpa.gov.cn/datasearch/home-index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-404"></a>dd-404 | 通知公告 | [医疗器械唯一标识数据库](https://udi.nmpa.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-405"></a>dd-405 | 通知公告 | [三品一械广告审批结果查询](https://spyxggsc.samr.gov.cn/spyxggsc/MDAIS) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-406"></a>dd-406 | 通知公告 | [中国医药信息查询平台](https://www.dayi.org.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-407"></a>dd-407 | 通知公告 | [医保药品目录查询](https://wx.nhsa.gov.cn/#/pages/NRDL/index/index) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-408"></a>dd-408 | 行业标准 | [药包材标准](http://app.nifdc.org.cn/jianybz/jybzTwoGj.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-409"></a>dd-409 | 行业标准 | [医疗器械标准](http://app.nifdc.org.cn/jianybz/jybzTwoGj.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-410"></a>dd-410 | 通知公告 | [国家药品监督管理局药品评审中心](https://www.cde.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-411"></a>dd-411 | 通知公告 | [矿业权市场](http://ky.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-412"></a>dd-412 | 通知公告 | [全国矿业权人勘查开采信息公示系统](https://kyqgs.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-413"></a>dd-413 | 通知公告 | [全球地质矿产信息系统](http://geoglobal.mnr.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-414"></a>dd-414 | 通知公告 | [全国地质资料馆](https://www.ngac.cn/125cms/c/qggnew/index.htm) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-415"></a>dd-415 | 通知公告 | [全国矿产地数据库](http://ngac.org.cn/Kuangchandi/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-416"></a>dd-416 | 通知公告 | [境外矿产资源开发备案](http://kczy.mofcom.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-417"></a>dd-417 | 通知公告 | [地质云](https://geocloud.cgs.gov.cn/#/home) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-418"></a>dd-418 | 通知公告 | [批准开采黄金矿产的企业名单](https://wap.miit.gov.cn/datainfo/zzsjk/pzkchjkcsqqymd/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-419"></a>dd-419 | 通知公告 | [公路企业经营资质查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jtbglqyzz/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-420"></a>dd-420 | 通知公告 | [道路运政经营业户查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jtbdlyzyh/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-421"></a>dd-421 | 通知公告 | [道路运政从业人员查询](https://ysfw.mot.gov.cn/NetRoadCGSS-web/information/query?searchType=person) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-422"></a>dd-422 | 通知公告 | [道路运政营运车辆查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jtbdlyzcl/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-423"></a>dd-423 | 通知公告 | [[民航局]资质查询](http://www.caac.gov.cn/FWDT/ZZCX/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-424"></a>dd-424 | 通知公告 | [国际货运代理企业备案](http://gjhy.mofcom.gov.cn/tableLoad.shtml) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-425"></a>dd-425 | 通知公告 | [航运企业经营资质查询（部本级数据）](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jtbsyqyzz/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-426"></a>dd-426 | 通知公告 | [船舶经营资质查询(部本级数据)](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jtbcbjjzz/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-427"></a>dd-427 | 通知公告 | [海员证书查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/jtbhyzs/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-428"></a>dd-428 | 通知公告 | [民航企业业绩查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/caacqyyj/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-429"></a>dd-429 | 通知公告 | [民航行程单验真](http://www.caac.gov.cn/INDEX/HLFW/DZKPYZ/) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-430"></a>dd-430 | 通知公告 | [铁路局行政许可公告查询](https://zwfw.nra.gov.cn/col/col66/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-431"></a>dd-431 | 行业标准 | [铁路技术标准信息查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/tljsbzxxfwpt/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-432"></a>dd-432 | 通知公告 | [试验检测人员考试合格查询](https://syjc.jtzyzg.org.cn/SYJC/LEAP/syjc/html/publicQuery.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-433"></a>dd-433 | 通知公告 | [全国出版物发行单位年度核验查询](https://www.nppa.gov.cn/cbwfxdwndhycx/cbwfxdwndhy_cxy/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-434"></a>dd-434 | 通知公告 | [全国只读类光盘复制单位光盘来源识别码查询](https://www.nppa.gov.cn/bsfw/cyjghcpcx/202112/P020211208753495011054.pdf) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-435"></a>dd-435 | 通知公告 | [行政审批事项清单](https://www.nppa.gov.cn/bsfw/xksx/xzxksxqd/201802/t20180208_4922.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-436"></a>dd-436 | 通知公告 | [图书出版单位](https://www.nppa.gov.cn/bsfw/cyjghcpcx/tcdw/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-437"></a>dd-437 | 通知公告 | [报纸/报社](https://www.nppa.gov.cn/bsfw/cyjghcpcx/bzbs/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-438"></a>dd-438 | 通知公告 | [期刊/期刊社](https://www.nppa.gov.cn/bsfw/cyjghcpcx/qkan/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-439"></a>dd-439 | 通知公告 | [音像出版单位](https://www.nppa.gov.cn/bsfw/cyjghcpcx/yxcb/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-440"></a>dd-440 | 通知公告 | [电子出版物出版单位](https://www.nppa.gov.cn/bsfw/cyjghcpcx/dzcb/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-441"></a>dd-441 | 通知公告 | [网络出版服务单位](https://www.nppa.gov.cn/bsfw/cyjghcpcx/wlcb/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-442"></a>dd-442 | 通知公告 | [发行机构](https://www.nppa.gov.cn/bsfw/cyjghcpcx/fxjg/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-443"></a>dd-443 | 通知公告 | [出版物信息查询](https://pdc.capub.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-444"></a>dd-444 | 通知公告 | [连续型电子出版物](https://www.nppa.gov.cn/bsfw/cyjghcpcx/lxdz/index.html) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-445"></a>dd-445 | 通知公告 | [记者证](https://press.nppa.gov.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-446"></a>dd-446 | 通知公告 | [中国电影数据信息网](https://www.zgdypw.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-447"></a>dd-447 | 通知公告 | [国家新闻出版广电总局电影电子政务平台](https://dy.chinafilm.gov.cn/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-448"></a>dd-448 | 通知公告 | [电影备案公示](https://www.chinafilm.gov.cn/xxgk/gsxx/dybalx/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-449"></a>dd-449 | 通知公告 | [电影公映许可证公示](https://www.chinafilm.gov.cn/xxgk/gsxx/dygyxkz/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-450"></a>dd-450 | 通知公告 | [国家新闻出版广电总局电视剧电子政务平台](https://dsj.nrta.gov.cn/index.shanty) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-451"></a>dd-451 | 通知公告 | [国家文物局信息查询服务](http://www.ncha.gov.cn/col/col2262/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-452"></a>dd-452 | 通知公告 | [电视剧备案公示](https://www.nrta.gov.cn/col/col8/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-453"></a>dd-453 | 通知公告 | [动画片备案公示](https://www.nrta.gov.cn/col/col2111/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-454"></a>dd-454 | 通知公告 | [重点网络影视剧信息备案系统](http://211.146.10.138:8080/YSJBA/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-455"></a>dd-455 | 通知公告 | [电子游戏审批](https://www.nppa.gov.cn/bsfw/jggs/yxspjg/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-456"></a>dd-456 | 通知公告 | [社会艺术水平考级机构查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/shysspkjjg/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-457"></a>dd-457 | 通知公告 | [全国文物保护工程资质单位数据库](http://gl.ncha.gov.cn/#/Industry/cul-protection-unit-info) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-458"></a>dd-458 | 通知公告 | [文化市场经营单位查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/whscjydw/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-459"></a>dd-459 | 通知公告 | [经营出境旅游业务旅行社名单查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/abroadnamelistjingwai/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-460"></a>dd-460 | 通知公告 | [社会组织活动备案查询](https://chinanpo.mca.gov.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-461"></a>dd-461 | 通知公告 | [体育竞赛信息查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/tyzjjsxxcx/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-462"></a>dd-462 | 通知公告 | [运动员技术等级查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/tyzjydyjsdjcx/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-463"></a>dd-463 | 通知公告 | [全国文物保护工程资格人员数据库](http://gl.ncha.gov.cn/#/Industry/industry-supervision-management-search) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-464"></a>dd-464 | 通知公告 | [12318全国文化市场举报平台](https://jbts.mct.gov.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-465"></a>dd-465 | 通知公告 | [农药产品生产批准证书名单](https://wap.miit.gov.cn/datainfo/zysjk/nycpscpzzsmd/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-466"></a>dd-466 | 通知公告 | [中国高等教育学生信息网(学信网)](https://www.chsi.com.cn/xlcx/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-467"></a>dd-467 | 通知公告 | [[教育部]阳光高考信息公开平台](https://gaokao.chsi.com.cn/gkzx.do) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-468"></a>dd-468 | 通知公告 | [[教育部]高校名单](http://www.moe.gov.cn/jyb_xxgk/s5743/s5744/A03/202110/t20211025_574874.html) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-469"></a>dd-469 | 通知公告 | [学位证书网上查询](https://ssza.psxjsz.com/v2/graduation?qhclickid=c5ce171436d5edb5) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-470"></a>dd-470 | 通知公告 | [国(境)外学历学位认证书在线查询](http://zwfwbl.cscse.edu.cn/homeView/contactUs?type=2) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-471"></a>dd-471 | 通知公告 | [中国民办学校网](http://www.hrmbedu.com.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-472"></a>dd-472 | 通知公告 | [国家职业资格证书全国联网查询](http://zscx.osta.org.cn/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-473"></a>dd-473 | 通知公告 | [国家职业资格证书查询](http://app.gjzwfw.gov.cn/jmopen/webapp/html5/tyzjgjzyzgzs/index.html) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-474"></a>dd-474 | 通知公告 | [中国人事考试网](http://www.cpta.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-475"></a>dd-475 | 通知公告 | [英语四六级考试成绩查询](https://cjcx.neea.edu.cn/html1/folder/21045/4883-1.htm) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-476"></a>dd-476 | 通知公告 | [普通话水平测试成绩查询](http://www.cltt.org/#/scoreQuery) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-477"></a>dd-477 | 通知公告 | [中国民办教育信息网](https://www.cnhsi.com.cn/hy) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-478"></a>dd-478 | 通知公告 | [中国少数民族汉语水平等级考试成绩查询](http://zwfw.moe.gov.cn/qualificat?activeType=3&addrida0a85a29922644e3a436aea87c34f567) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-479"></a>dd-479 | 通知公告 | [全国民办高校学生信息公共查询平台](http://www.cxedu.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-480"></a>dd-480 | 通知公告 | [中小学教师资格考试成绩查询](http://zwfw.moe.gov.cn/qualificat?activeType=4&addrida87d9521316442258d3275fe1e790bee) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-481"></a>dd-481 | 通知公告 | [中国民办教育公共信息服务平台](https://cnhsi.com.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |

#### 1.1.10 政府采购、投资审批与历史页脚保留号（共 10 项）

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-482"></a>dd-482 | 通知公告 | [中国政府采购网](http://www.ccgp.gov.cn/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-483"></a>dd-483 | 通知公告 | [政府采购代理机构名单](http://www.ccgp.gov.cn/agency/md/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-484"></a>dd-484 | 通知公告 | [政府采购合同公示](http://htgs.ccgp.gov.cn/GS8/contractpublish/search) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-485"></a>dd-485 | 通知公告 | [中国招标投标协会](http://www.ctba.org.cn/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-486"></a>dd-486 | 通知公告 | [全国公共资源交易平台-招投标信息](http://www.cebpubservice.com/NewIndex/index.shtml) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-487"></a>dd-487 | 通知公告 | [中国招标投标公共服务平台](http://bulletin.cebpubservice.com/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-488"></a>dd-488 | 通知公告 | [[发改委]全国投资项目在线审批监管平台](http://www.tzxm.gov.cn/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-489"></a>dd-489 | 其他 | [全国互联网安全管理服务平台（网安备案页脚噪声）](http://www.beian.gov.cn/portal/registerSystemInfo?recordcode=11010502039017) | 排除 | 不适配（页脚噪声） | [ ] | 源文档提取时的页脚备案链接噪声，保留历史编号，不做采集。 |
| <a id="dd-490"></a>dd-490 | 其他 | [工信部电信业务市场综合管理信息系统（ICP证页脚噪声）](https://dxzhgl.miit.gov.cn/dxxzsp/xkz/xkzgl/resource/qiyesearch.jsp?num=%25E4%25BA%25ACICP%25E8%25AF%2581110182%25E5%258F%25B7&type=xuke) | 排除 | 不适配（页脚噪声） | [ ] | 源文档提取时的页脚备案链接噪声，保留历史编号，不做采集。 |
| <a id="dd-491"></a>dd-491 | 其他 | [工信部政务服务平台（ICP备案页脚噪声）](http://beian.miit.gov.cn/) | 排除 | 不适配（页脚噪声） | [ ] | 源文档提取时的页脚备案链接噪声，保留历史编号，不做采集。 |

### 1.2 中国香港与中国澳门 (Hong Kong & Macau)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-007"></a>dd-007 | 其他 | [中国香港公司注册处综合资讯系统](https://www.icris.cr.gov.hk/csci/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-008"></a>dd-008 | 其他 | [中国澳门商业登记讯息平台](https://eservice.dsaj.gov.mo/dsajservice9/commercial-platform/index.xhtml) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-076"></a>dd-076 | 通知公告 | [香港国际仲裁中心（HKIAC）](https://www.hkiac.org/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-130"></a>dd-130 | 其他 | [中国香港知识产权署](http://ipsearch.ipd.gov.hk/index.html) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-131"></a>dd-131 | 其他 | [中国澳门经济局](https://www.economia.gov.mo/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-137"></a>dd-137 | 其他 | [中国香港专利检索](https://esearch.ipd.gov.hk/nis-pos-view/#/pt/quicksearch) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-148"></a>dd-148 | 其他 | [中国香港商标检索](https://esearch.ipd.gov.hk/nis-pos-view/#/tm/quicksearch?lang=en) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-161"></a>dd-161 | 财务报告 | [香港交易所披露易（HKEXnews）](https://www.hkexnews.hk/index_c.htm) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-505"></a>dd-505 | 法律法规 | [电子版香港法例（Hong Kong e-Legislation, HKeL）](https://www.elegislation.gov.hk/) | P0 | `web` | [ ] | 具法律地位的香港官方成文法数据库，支持按章号（Cap.）与版本日期获取 PDF。 |
| <a id="dd-506"></a>dd-506 | 案例 | [香港司法机构法律参考资料系统（LRS）](https://legalref.judiciary.hk/) | P0 | `web` / `worldwidelaw_bridge` | [ ] | 香港各级法院判案书与裁决理由官方库，已有 WWL `HK` 开源抓取先例。 |
| <a id="dd-507"></a>dd-507 | 税务资料 | [香港税务局（IRD）释义及执行指引（DIPN）](https://www.ird.gov.hk/) | P1 | `web` | [ ] | 公开香港税务条例指引（DIPN）与税务上诉委员会（Board of Review）裁决。 |
| <a id="dd-508"></a>dd-508 | 法律法规 | [澳门印务局（Imprensa Oficial）公报与法例](https://www.io.gov.mo/) | P1 | `web` / `worldwidelaw_bridge` | [ ] | 澳门特别行政区公报与官方成文法入口，已有 WWL `MO` 抓取先例。 |

### 1.3 中国台湾 (Taiwan)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-132"></a>dd-132 | 其他 | [中国台湾经济部智慧财产局](https://www.tipo.gov.tw/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-162"></a>dd-162 | 财务报告 | [台湾证券交易所](https://www.twse.com.tw/zh/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-509"></a>dd-509 | 法律法规 | [全国法规资料库（LawBank Taiwan）](https://law.moj.gov.tw/ENG/index.aspx) | P0 | `web` / `structured_xml` | [ ] | 提供台湾地区现行法规、行政规则、英译及政府开放资料（JSON/XML/CSV）下载。 |
| <a id="dd-510"></a>dd-510 | 案例 | [司法院法学资料检索系统（裁判书查询）](https://judgment.judicial.gov.tw/) | P1 | `web` | [ ] | 公开各级法院裁判书及司法院开放数据批次压缩包，需核对批次包更新周期。 |
| <a id="dd-511"></a>dd-511 | 税务资料 | [财政部主管法规共用系统（赋税法规与解释函令）](https://law-out.mof.gov.tw/) | P1 | `web` | [ ] | 集中公开台湾地区税法条文、财政部解释函令与立法沿革。 |

### 1.4 越南 (Vietnam)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-492"></a>dd-492 | 法律法规 | [越南政府文献门户（vanban.chinhphu.vn）](https://vanban.chinhphu.vn/) | P0 | `web`（[`dd-492.yaml`](../../sources/dd-492.yaml)） | [x] | **实网已跑通**：已验证 ASP.NET DoPostBack 列表、越语详情题录（`Số ký hiệu` 85/2026/NĐ-CP 等）与附件下载、PDF 扫描判定及 OCR 主备切换 + 离线 `reparse`。 |
| <a id="dd-493"></a>dd-493 | 法律法规 | [Công báo Chính phủ（越南电子公报）](https://congbao.chinhphu.vn/) | P0 | `web` / `worldwidelaw_bridge`（[`dd-493.yaml`](../../sources/dd-493.yaml)） | [x] | **实网已跑通**：已验证公报详情页（如 `48/2026/QĐ-TTg`）题录提取、`g7.cdnchinhphu.vn` 附件流（补齐 GlobalSign RSA OV SSL CA 2018 中间证书链）DOCX/PDF 原件下载归档、扫描页 OCR 主备切换 + 离线 `reparse`（`external_requests=0`）。 |
| <a id="dd-494"></a>dd-494 | 法律法规 | [Cơ sở dữ liệu quốc gia về văn bản pháp luật (VBPL)](https://vbpl.vn/) | P0 | `web`（[`dd-494.yaml`](../../sources/dd-494.yaml)） | [ ] | **实测受阻（源站改版 + WAF 403）**：站点已由旧版 ASP.NET 改版为 Next.js 并接入前置 PWS CDN/WAF（注入 `/_fec_sbu/fec_wrapper.js` 动态挑战，对纯 HTTP 请求全站含 `/robots.txt` 返回 403，且网关偶发返回自签名拦截证书链）；越南中央法规与公报由已实网跑通的 `dd-492` 与 `dd-493` 覆盖。 |
| <a id="dd-495"></a>dd-495 | 法律法规 | [LuatVietnam 法规库](https://luatvietnam.vn/) | P0 | `web`（受控检索） | [ ] | 商业法规库，部分元数据公开，下载与高级功能需登录，仅作辅助交叉核验。 |
| <a id="dd-496"></a>dd-496 | 案例 | [LuatVietnam 判决库](https://luatvietnam.vn/ban-an/) | P0 | `web`（受控检索） | [ ] | 商业判决库，需核对服务条款与访问频控。 |
| <a id="dd-497"></a>dd-497 | 法律法规 | [Thư Viện Pháp Luật 法规库](https://thuvienphapluat.vn/page/tim-van-ban.aspx) | P0 | `web`（受控检索） | [ ] | 含 Cloudflare 防护与登录限制，不建议高频深抓。 |
| <a id="dd-498"></a>dd-498 | 案例 | [Thư Viện Pháp Luật 判决库](https://thuvienphapluat.vn/banan/) | P0 | `web`（受控检索） | [ ] | 含 Cloudflare 防护与会员限制，仅作公开题录辅助核对。 |
| <a id="dd-512"></a>dd-512 | 法律法规 | [越南国会法律文书系统（Văn bản quy phạm pháp luật Quốc hội）](https://quochoi.vn/tintuc/Pages/van-ban-phap-luat.aspx) | P0 | `web` | [ ] | 越南国会立法与决议官方入口，可与 `dd-492` / `dd-494` 交叉核验。 |
| <a id="dd-513"></a>dd-513 | 法律法规 | [越南政府门户政策与法规建设专栏（Xây dựng chính sách, pháp luật）](https://xaydungchinhsach.chinhphu.vn/) | P0 | `web` | [ ] | 越南政府官方立法草案、新规解读与规范性文件动态发布门户。 |
| <a id="dd-514"></a>dd-514 | 案例 | [越南最高人民法院判例门户（Án lệ TANDTC）](https://anle.toaan.gov.vn/) | P0 | `web` | [ ] | 越南最高人民法院正式公布的指导性判例（Án lệ）集合，体量可控、结构清晰。 |
| <a id="dd-515"></a>dd-515 | 案例 | [越南最高人民法院电子门户公开判决库（Công bố bản án, quyết định）](https://congbobanan.toaan.gov.vn/) | P0 | `web`（浏览器渲染/验证码识别） | [ ] | 越南全国各级法院公开裁判文书官方库，存在检索校验码与会话频控，需低速受控采集。 |
| <a id="dd-516"></a>dd-516 | 税务资料 | [越南财政部 / 税务总局法规与公文库（Bộ Tài chính / Tổng cục Thuế）](https://gdt.gov.vn/) | P0 | `web` | [ ] | 包含越南税收法规、部委通告（Thông tư）与税务指导公文（Công văn，配合 [越南财政部](https://mof.gov.vn/)）。 |

### 1.5 日本 (Japan)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-143"></a>dd-143 | 其他 | [日本专利信息平台](https://www.j-platpat.inpit.go.jp/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-517"></a>dd-517 | 法律法规 | [e-Gov 法令检索 / 法令 API v2（デジタル庁）](https://laws.e-gov.go.jp/) | P0 | `structured_xml`（e-Gov API v2） | [ ] | 日本数字厅官方支持 XML/JSON API v2 与批量下载，已实测 `200 OK`，最适合原生结构化接入。 |
| <a id="dd-518"></a>dd-518 | 政策文件 | [互联网版官报（インターネット版官報）](https://www.kanpo.go.jp/) | P1 | `web` | [ ] | 官方公开直连，免费公开直近 90 日官报全文，90 日以前受检索/付费限制，必须建设每日增量归档。 |
| <a id="dd-519"></a>dd-519 | 案例 | [裁判所判例检索系统（Courts in Japan）](https://www.courts.go.jp/app/hanrei_jp/search1) | P1 | `web` | [ ] | 日本最高裁、高裁、下级裁、行政及知产判例官方检索与裁判书 PDF。 |
| <a id="dd-520"></a>dd-520 | 税务资料 | [国税厅法令解释通达、质义应答与文书回答事例（NTA）](https://www.nta.go.jp/law/index.htm) | P1 | `web` | [ ] | 国税厅官方集中公开税法通达、问答事例、文书回答，辅以 [国税不服审判所](https://www.kfs.go.jp/) 裁决事例。 |

### 1.6 韩国 (South Korea)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-142"></a>dd-142 | 其他 | [韩国特许厅（KIPO）](https://www.kipo.go.kr/ko/MainApp.do) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-521"></a>dd-521 | 法律法规 | [国家法令情报中心（국가법령정보센터 &amp; LAW Open API）](https://www.law.go.kr/) | P0 | `structured_xml`（LAW Open API） | [ ] | 提供法令、行政规则、自治法规、判例、宪法裁判所决定与条约 API；需申请 OC 账号参数。 |
| <a id="dd-522"></a>dd-522 | 政策文件 | [大韩民国电子官报（대한민국 전자관보）](https://gwanbo.go.kr/) | P1 | `web` | [ ] | 韩国行政安全部官方电子官报，按日发布法律公布与行政公告。 |
| <a id="dd-523"></a>dd-523 | 案例 | [大法院综合法律情报门户（대법원 종합법률정보）](https://glaw.scourt.go.kr/) | P1 | `web`（浏览器会话） | [ ] | 韩国大法院判例、规则、例规与先例官方库，需维护检索 Session。 |
| <a id="dd-524"></a>dd-524 | 税务资料 | [国税法令情报系统（국세법령정보시스템）与租税审判院](https://txsi.hometax.go.kr/) | P1 | `web` | [ ] | 包含国税法令、解释例规、审查请求与 [租税审判院](https://www.tt.go.kr/) 裁决例。 |

### 1.7 新加坡 (Singapore)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-091"></a>dd-091 | 通知公告 | [新加坡国际仲裁中心（SIAC）](https://siac.org.sg/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-166"></a>dd-166 | 财务报告 | [新加坡交易所（SGX）](https://www.sgx.com/zh-hans/securities/company-announcements) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-525"></a>dd-525 | 法律法规 | [Singapore Statutes Online (SSO)](https://sso.agc.gov.sg/) | P0 | `web` / `worldwidelaw_bridge` | [ ] | 总检察署官方成文法库，但是实测触发 Cloudflare `403 Just a moment`，需受控浏览器会话或低频缓存。 |
| <a id="dd-526"></a>dd-526 | 案例 | [eLitigation 新加坡法院判决库](https://www.elitigation.sg/_layouts/IELS/HomePage/Pages/Home.aspx) | P0 | `web` / `worldwidelaw_bridge` | [ ] | 公开 2000 年以来最高法院、国家法院与家事法院判决，WWL 已有 `SG` 抓取先例。 |
| <a id="dd-527"></a>dd-527 | 税务资料 | [新加坡国内税务局（IRAS）e-Tax Guides](https://www.iras.gov.sg/) | P1 | `web` | [ ] | 官方公开各税种电子税务指南（e-Tax Guides PDF）与预提税/激励政策说明。 |

### 1.8 其他亚洲法域：马来西亚、印尼、印度、泰国、菲律宾、阿联酋 (Other Asian Jurisdictions)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-528"></a>dd-528 | 法律法规 | [马来西亚联邦电子宪报与法律门户（Federal Gazette / LOM）](https://lom.agc.gov.my/) | P1 | `web` | [ ] | 马来西亚总检察署官方联邦宪报与成文法汇编 PDF（配合 [LOM 门户](https://lom.agc.gov.my/)）。 |
| <a id="dd-529"></a>dd-529 | 案例 | [马来西亚联邦法院与上诉院公开判决（e-Judgments Malaysia）](https://kehakiman.gov.my/) | P2 | `web` | [ ] | 马来西亚司法机构官方公布的最新法院判词（Grounds of Judgment）。 |
| <a id="dd-530"></a>dd-530 | 税务资料 | [马来西亚内陆税收局（LHDN / IRBM）Public Rulings &amp; Guidelines](https://www.hasil.gov.my/) | P1 | `web` | [ ] | 官方公开税务公共裁定（Public Rulings）与实务指引 PDF。 |
| <a id="dd-531"></a>dd-531 | 法律法规 | [印尼国家法律文献信息网与法规库（JDIH BPK / Peraturan.go.id）](https://peraturan.bpk.go.id/) | P1 | `web` | [ ] | 印尼审计署 JDIH BPK 与法务部 [Peraturan.go.id](https://peraturan.go.id/) 结构清晰、覆盖广、PDF 附件规整。 |
| <a id="dd-532"></a>dd-532 | 案例 | [印尼最高法院裁判文书目录（Direktori Putusan Mahkamah Agung）](https://putusan3.mahkamahagung.go.id/) | P1 | `web` | [ ] | 超过百万份公开判决书，含 HTML 元数据与 PDF 原件，需严格控制并发与速率。 |
| <a id="dd-533"></a>dd-533 | 税务资料 | [印尼财政部 JDIH 与税务总局（DJP）法规库](https://jdih.kemenkeu.go.id/) | P1 | `web` | [ ] | 印尼财政部财税规章（PMK）与 [税务总局 DJP](https://www.pajak.go.id/) 通告（PER/SE）。 |
| <a id="dd-534"></a>dd-534 | 法律法规 | [印度国家法典门户与电子宪报（India Code / eGazette）](https://www.indiacode.nic.in/) | P1 | `web` | [ ] | 印度中央与各邦成文法官方库（配合 [电子宪报 eGazette](https://egazette.gov.in/)）。 |
| <a id="dd-535"></a>dd-535 | 案例 | [印度最高法院判决库与 eCourts（SCI Judgments / eSCR）](https://www.sci.gov.in/) | P1 | `web` | [ ] | 印度最高法院 [电子判例汇编 eSCR](https://digiscr.sci.gov.in/) 与 [全国高院判决入口](https://judgments.ecourts.gov.in/)；存在验证码，公开 AWS S3 镜像数据集可作补充。 |
| <a id="dd-536"></a>dd-536 | 税务资料 | [印度所得税局与间接税及海关中央委员会（Income Tax / CBIC）](https://incometaxindia.gov.in/) | P1 | `web` | [ ] | 公开直接税与 GST/关税法案、通知（Notifications）与通告（Circulars，配合 [CBIC 税讯门户](https://taxinformation.cbic.gov.in/)）。 |
| <a id="dd-537"></a>dd-537 | 法律法规 | [泰国皇家政府公报与国务委员会法制库（Royal Gazette / Krisdika）](https://ratchakitcha.soc.go.th/) | P2 | `web` | [ ] | 泰语皇家公报 PDF 与[国务委员会法规库](https://www.ocs.go.th/)，多扫描件或泰语编码需配合 OCR。 |
| <a id="dd-538"></a>dd-538 | 税务资料 | [泰国税务局（Revenue Department）税法与解释库](https://www.rd.go.th/) | P2 | `web` | [ ] | 公开泰国《税法典》（Revenue Code）、皇家法令与税务局通告解释。 |
| <a id="dd-539"></a>dd-539 | 法律法规 | [菲律宾官方公报与最高法院电子图书馆（Official Gazette / SC E-Library）](https://www.officialgazette.gov.ph/) | P2 | `web` | [ ] | 公开共和国法案（Republic Acts）、总统行政令及 [最高法院电子图书馆](https://elibrary.judiciary.gov.ph/) 判例。 |
| <a id="dd-540"></a>dd-540 | 税务资料 | [菲律宾国内税务局（BIR）Revenue Regulations &amp; Rulings](https://www.bir.gov.ph/) | P2 | `web` | [ ] | 公开税收条例（RR）、税务备忘通告（RMC）与税务命令（RMO）。 |
| <a id="dd-541"></a>dd-541 | 法律法规 | [阿联酋联邦法律门户（UAE Legislation）与联邦税务局（FTA）](https://uaelegislation.gov.ae/) | P1 | `web` | [ ] | 阿英双语官方联邦法律与内阁决议，配合 [联邦税务局 FTA](https://tax.gov.ae/) 企业税与增值税指南。 |
| <a id="dd-542"></a>dd-542 | 案例 | [迪拜国际金融中心法院（DIFC Courts）与 ADGM 法院判决库](https://www.difccourts.ae/) | P2 | `web` | [ ] | 全英文普通法商事判例与程序规则，结构清晰。 |

---

## 2. 欧洲法域 (Europe)

### 2.1 欧盟及欧洲跨国机构 (European Union & Council of Europe)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-139"></a>dd-139 | 其他 | [EuropeanPatentOffice](https://www.epo.org/) | P1 | `web` | [ ] | 静态正文或名单页，可直接编写 `web` XPath 规则适配。 |
| <a id="dd-141"></a>dd-141 | 其他 | [欧盟知识产权局（EUIPO）](https://www.euipo.europa.eu/en) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-150"></a>dd-150 | 其他 | [欧盟商标查询系统](https://eutms.gippc.com.cn/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-543"></a>dd-543 | 法律法规 | [EUR-Lex 欧盟法律门户（CELLAR REST / SPARQL / Bulk）](https://eur-lex.europa.eu/) | P0 | `structured_xml`（Formex / Akoma Ntoso） | [ ] | 按 CELEX 编号组织，官方提供网页服务、SPARQL 与批量数据包，已实测 `200 OK`。 |
| <a id="dd-544"></a>dd-544 | 案例 | [欧盟法院（CURIA / InfoCuria）判例库](https://curia.europa.eu/) | P0 | `web` | [ ] | 收录欧盟法院与普通法院判决、裁定及总法律顾问意见书（多语种）。 |
| <a id="dd-545"></a>dd-545 | 案例 | [欧洲人权法院判例库（HUDOC - ECHR）](https://hudoc.echr.coe.int/) | P1 | `web`（JSON 接口） | [ ] | 结构化 JSON 检索接口，覆盖欧洲委员会成员国人权与司法判例。 |
| <a id="dd-546"></a>dd-546 | 税务资料 | [欧盟委员会税务与海关同盟（DG TAXUD）法规与数据库](https://taxation-customs.ec.europa.eu/) | P1 | `web` | [ ] | 公开欧盟增值税指令（VAT）、关税规则、支柱二（Pillar 2）及跨国税制协调文件。 |

### 2.2 英国 (United Kingdom)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-009"></a>dd-009 | 其他 | [英国公司注册处（Companies House）](https://find-and-update.company-information.service.gov.uk/) | P1 | `web`（参数/接口） | [ ] | 公开参数查询或 API 入口，可按固定查询参数归档。 |
| <a id="dd-089"></a>dd-089 | 通知公告 | [伦敦国际仲裁院（LCIA）](https://www.lcia.org/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-151"></a>dd-151 | 其他 | [TheIntellectualPropertyOffice](https://www.gov.uk/government/organisations/intellectual-property-office) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-167"></a>dd-167 | 财务报告 | [伦敦证券交易所（LSE）](https://www.londonstockexchange.com/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-547"></a>dd-547 | 法律法规 | [英国国家立法库（legislation.gov.uk）](https://www.legislation.gov.uk/) | P0 | `structured_xml` / `worldwidelaw_bridge` | [ ] | 全球最规范的开放立法 API 之一，任何法规 URL 加 `/data.xml` 或 `/data.akn` 即可获结构化全文，Atom feed 支持增量，已实测 `200 OK`。 |
| <a id="dd-548"></a>dd-548 | 案例 | [英国国家档案馆判例库（Find Case Law - National Archives）](https://caselaw.nationalarchives.gov.uk/) | P0 | `structured_xml` | [ ] | 提供 LegalDocML (Akoma Ntoso) XML 与开放许可 API，已实测 `200 OK`；需遵守开放司法许可中关于禁止重识别及自动化决策的附加条款。 |
| <a id="dd-549"></a>dd-549 | 税务资料 | [英国税务海关总署内部手册（HMRC Internal Manuals）与税务裁判所判决](https://www.gov.uk/government/collections/hmrc-manuals) | P1 | `web`（GOV.UK Content API） | [ ] | HMRC 全部税种内部手册均可通过 GOV.UK Content API（`/api/content/...`）获取结构化 JSON；另见 [税务与衡平裁判所判决](https://www.gov.uk/tax-and-chancery-tribunal-decisions)。 |

### 2.3 德国 (Germany)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-011"></a>dd-011 | 其他 | [德国企业信息门户（Firmenwissen）](http://www.firmenwissen.de/index.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-144"></a>dd-144 | 其他 | [DPMAregister](https://register.dpma.de/DPMAregister/Uebersicht?lang=en) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-550"></a>dd-550 | 法律法规 | [德国联邦司法部法律库（Gesetze im Internet）与联邦法律公报（BGBl.）](https://www.gesetze-im-internet.de/) | P0 | `structured_xml` | [ ] | 官方提供全量/单部联邦法律 XML 压缩包索引（`gii-toc.xml`），[联邦法律公报](https://www.recht.bund.de/) 自 2023 年起免费公开官方 PDF，已实测 `200 OK`。 |
| <a id="dd-551"></a>dd-551 | 案例 | [德国联邦最高法院及七大联邦法院判例库（Rechtsprechung im Internet）](https://www.rechtsprechung-im-internet.de/) | P0 | `structured_xml` / `oldp_bridge`（[`dd-551.yaml`](../../sources/dd-551.yaml)） | [x] | **实网已跑通（`de-bgh`）**：已验证 `oldp_bridge` 采集德国联邦最高法院（BGH 9. Zivilsenat，`IX ZB 72/08`）官方 XML ZIP 原件归档、结构化字段提取 + 离线 `reparse`（`external_requests=0`），并兼容 Open Legal Data API。 |
| <a id="dd-552"></a>dd-552 | 税务资料 | [德国联邦财政部（BMF-Schreiben）与联邦财税法院（BFH）](https://www.bundesfinanzministerium.de/) | P1 | `web` | [ ] | 公开德国联邦财政部税务通函（BMF-Schreiben）与[联邦财税法院 BFH](https://www.bundesfinanzhof.de/)裁判。 |

### 2.4 法国 (France)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-553"></a>dd-553 | 法律法规 | [法国国家法律门户（Légifrance / PISTE API / DILA Open Data）](https://www.legifrance.gouv.fr/) | P0 | `structured_xml` / `worldwidelaw_bridge` | [ ] | DILA 官方提供 PISTE OAuth2 API 及全量开放数据包（LEGI/JORF/KALI/CASS/JADE，`echanges.dila.gouv.fr`），无需硬爬网页。 |
| <a id="dd-554"></a>dd-554 | 案例 | [法国最高法院（Cour de cassation - Judilibre API）与最高行政法院（ArianeWeb）](https://www.courdecassation.fr/) | P1 | `web`（PISTE Judilibre API） | [ ] | 法国最高法院通过 Judilibre 开放全量匿名化判决 API，最高行政法院通过 [ArianeWeb](https://www.conseil-etat.fr/arianeweb) 公开行政与税务判例。 |
| <a id="dd-555"></a>dd-555 | 税务资料 | [法国公共财政官方公报税务库（BOFiP-Impôts）](https://bofip.impots.gouv.fr/) | P0 | `structured_xml` | [ ] | 法国官方税务解释库，除在线检索外还在 `data.gouv.fr` 提供开放归档数据包下载。 |

### 2.5 瑞士 (Switzerland)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-012"></a>dd-012 | 其他 | [瑞士联邦企业识别号登记簿（UID Register）](https://www.uid.admin.ch/search.aspx?lang=en) | 暂缓 | 暂缓（输入/登录型） | [ ] | 需输入特定主体参数或登录授权，无公开全量列表，按需单点核验。 |
| <a id="dd-556"></a>dd-556 | 案例 | [瑞士联邦法律平台（Fedlex）与联邦最高法院（BGer）及联邦税务局（ESTV）](https://www.fedlex.admin.ch/) | P0 | `structured_xml` / `opencaselaw_bridge`（[`dd-556.yaml`](../../sources/dd-556.yaml)） | [x] | **实网已跑通（`ch-bger`）**：已验证 `opencaselaw_bridge` 对接 `mcp.opencaselaw.ch` 采集瑞士联邦最高法院（Bundesgericht - BGer，`7F_22/2026`）判例列表与裁判全文 + 离线 `reparse`（`external_requests=0`）；`bger.ch` 官网直连受 Imperva Incapsula WAF 拦截。 |

### 2.6 瑞典 (Sweden)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-090"></a>dd-090 | 通知公告 | [斯德哥尔摩商会仲裁院（SCC）](https://sccarbitrationinstitute.se/en/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |

---

## 3. 美洲法域 (Americas)

### 3.1 美国 (United States)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-140"></a>dd-140 | 其他 | [美国专利商标局（USPTO）](https://www.uspto.gov/) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-164"></a>dd-164 | 财务报告 | [美国证监会 EDGAR（SEC EDGAR）](https://www.sec.gov/edgar/searchedgar/companysearch.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-165"></a>dd-165 | 财务报告 | [纳斯达克交易所（Nasdaq）](https://www.sec.gov/edgar/searchedgar/companysearch.html) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-499"></a>dd-499 | 法律法规 | [Federal Register (federalregister.gov)](https://www.federalregister.gov/developers/documentation/api/v1) | P2 | `federal_register`（[`dd-499.yaml`](../../sources/dd-499.yaml)） | [x] | **实网已跑通**：已验证 JSON API 列表 + RFC 6901 JSON Pointer + 全文 XML 转 Markdown + GPO PDF 原件归档（`2026-07625`）+ 离线 `reparse`。 |
| <a id="dd-557"></a>dd-557 | 法律法规 | [GovInfo (U.S. Government Publishing Office API &amp; Bulk Data)](https://www.govinfo.gov/) | P0 | `structured_xml`（GovInfo API / Bulk） | [ ] | 官方提供 CFR、FR、USCODE、BILLS、STATUTE 全量 XML/PDF Bulk 仓库与 API（支持免费申请 `api.data.gov` Key）。 |
| <a id="dd-558"></a>dd-558 | 法律法规 | [美国众议院《美国法典》官方门户（OLRC - uscode.house.gov）](https://uscode.house.gov/download/download.shtml) | P0 | `structured_xml`（USLM XML） | [ ] | 提供《美国法典》全部 Title 的官方 USLM XML、XHTML 与 PDF 批量包直链下载，无需任何 Key。 |
| <a id="dd-559"></a>dd-559 | 法律法规 | [电子联邦法规汇编（eCFR API - ecfr.gov）](https://www.ecfr.gov/developers/documentation/api/v1) | P0 | `structured_xml`（eCFR REST/XML） | [ ] | 提供按日期切面的美国联邦行政法规（CFR）完整结构化 XML 与 JSON 元数据，已实测 `200 OK`。 |
| <a id="dd-560"></a>dd-560 | 法律法规 | [Open States 美国 50 州立法与法案聚合](https://openstates.org/) | P1 | `openstates_bridge`（[`dd-560.yaml`](../../sources/dd-560.yaml)） | [x] | **实网已跑通**：已通过 `.env` 配置 `OPENSTATES_API_KEY`，验证 `openstates_bridge` 对接 `v3.openstates.org/bills` 获取华盛顿州法案（`HB 1345`）并顺着版本链接从 `lawfilesext.leg.wa.gov` 下载 3 个官方 PDF 原件版本（`Passed Legislature` / `Bill` / `Engrossed Bill`）直提入库 + 离线 `reparse`（`external_requests=0`）。 |
| <a id="dd-561"></a>dd-561 | 案例 | [CourtListener / Free Law Project &amp; Juriscraper（美国联邦与各州判例）](https://www.courtlistener.com/) | P0 | `juriscraper_bridge`（[`dd-561.yaml`](../../sources/dd-561.yaml)） | [x] | **实网已跑通（`us-ca1`）**：已验证 `juriscraper_bridge` 对接 CourtListener REST API v4 检索美国联邦第一巡回上诉法院（`ca1`，`25-1348`）判例、下载 `storage.courtlistener.com` 判决书 PDF 原件直提 + 离线 `reparse`（`external_requests=0`）。 |
| <a id="dd-562"></a>dd-562 | 案例 | [美国联邦最高法院官网（supremecourt.gov）与 Caselaw Access Project (CAP)](https://www.supremecourt.gov/opinions/opinions.aspx) | P1 | `web` / `structured_xml` | [ ] | 最高法院官网公开 Slip Opinions 与 Bound Volumes PDF；哈佛法学院 [CAP](https://case.law/)开放美国历史判例数据集。 |
| <a id="dd-563"></a>dd-563 | 税务资料 | [美国国税局（IRS Internal Revenue Bulletins / Written Determinations）与美国税务法院（DAWSON）](https://www.irs.gov/irb) | P0 | `web` | [ ] | 公开 IRS 税收公报（IRB）、[私信裁定 PLRs/TAMs](https://www.irs.gov/written-determinations) 与 [美国税务法院 DAWSON](https://dawson.ustaxcourt.gov/) 判例。 |

### 3.2 加拿大与英属维尔京群岛 (Canada & BVI)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-010"></a>dd-010 | 其他 | [BVI 金融服务委员会（BVI Financial Services Commission）](http://www.bvifsc.vg/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-013"></a>dd-013 | 其他 | [加拿大联邦企业登记库（Corporations Canada）](https://www.ic.gc.ca/app/scr/cc/CorporationsCanada/fdrlCrpSrch.html?locale=en_CA) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-564"></a>dd-564 | 法律法规 | [加拿大司法部联邦法规库（Justice Laws Website &amp; GitHub XML）](https://laws-lois.justice.gc.ca/) | P0 | `structured_xml` | [ ] | 加拿大司法部在官网与[官方 GitHub 仓库](https://github.com/justicecanada/laws-lois-xml)同步开源全套英法双语联邦法律与法规 XML，极易全量接入。 |
| <a id="dd-565"></a>dd-565 | 案例 | [加拿大最高法院（SCC / Lexum）、联邦法院与 CanLII](https://decisions.scc-csc.ca/) | P1 | `web` | [ ] | 加拿大最高法院、联邦法院与[加拿大税务法院](https://decision.tcc-cci.gc.ca/)均基于 Lexum 平台公开判决；CanLII 提供 API（需申请 Key）。 |
| <a id="dd-566"></a>dd-566 | 税务资料 | [加拿大税务局（CRA Income Tax Folios, Circulars &amp; Rulings）](https://www.canada.ca/en/revenue-agency.html) | P1 | `web` | [ ] | 公开所得税专题指南（Income Tax Folios）、信息通告（Information Circulars）与 GST/HST 备忘录。 |

---

## 4. 大洋洲法域 (Oceania)

### 4.1 澳大利亚 (Australia)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-014"></a>dd-014 | 其他 | [澳大利亚商业登记簿（ABR Lookup）](http://abr.business.gov.au/) | 待复查 | 待复查（连接/风控限制） | [ ] | 历史探测存在连接超时、WAF 或状态码限制，适配前需复测可达性。 |
| <a id="dd-567"></a>dd-567 | 法律法规 | [澳大利亚联邦立法登记库（Federal Register of Legislation）](https://www.legislation.gov.au/) | P0 | `web` | [ ] | 澳大利亚官方权威成文法、条例与公报库，OpenAustralia / AustLII 提供开源解析参考。 |
| <a id="dd-568"></a>dd-568 | 案例 | [澳大利亚高等法院（HCA）、联邦法院与 AustLII（Open Australian Legal Corpus）](https://www.hcourt.gov.au/) | P1 | `web` | [ ] | 高等法院与联邦法院公开判决，且 HuggingFace `isaacus/open-australian-legal-corpus` 已汇总超过 22 万篇开放许可法规与判例。 |
| <a id="dd-569"></a>dd-569 | 税务资料 | [澳大利亚税务局法律数据库（ATO Legal Database）](https://www.ato.gov.au/law) | P1 | `web` | [ ] | 集中公开 ATO 税收裁定（TR/TD/GSTR）、实务声明（PS LA）与判例影响声明。 |

### 4.2 新西兰 (New Zealand)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-570"></a>dd-570 | 法律法规 | [新西兰立法门户（New Zealand Legislation - PCO）](https://www.legislation.govt.nz/) | P0 | `structured_xml` | [ ] | 新西兰议会法律起草办公室（PCO）提供官方 XML 架构与开放许可法规数据（`/subscribe/` 目录可直接遍历 XML）。 |
| <a id="dd-571"></a>dd-571 | 税务资料 | [新西兰司法部判决库（NZLII / Courts of NZ）与税务局税务政策（IRD Tax Policy）](https://www.courtsofnz.govt.nz/) | P1 | `web` | [ ] | 公开新西兰高级法院判决与[税务局政策库](https://www.taxtechnical.ird.govt.nz/)税务裁定、解释声明（Interpretation Statements）。 |

---

## 5. 非洲法域与国际组织 (Africa & International Organizations)

### 5.1 非洲开放法律联盟 (Africa — South Africa, Kenya, Nigeria, Tanzania, Uganda)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-572"></a>dd-572 | 法律法规 | [Laws.Africa Commons &amp; 非洲各国 LII 联盟（SouthLII / Kenya Law / TanzLII 等）](https://commons.laws.africa/) | P1 | `structured_xml`（Akoma Ntoso） | [ ] | 采用统一 Akoma Ntoso XML 标准，遵循开放许可提供南非、肯尼亚、尼日利亚、坦桑尼亚、乌干达等国政府公报、成文法与高院判例。 |

### 5.2 国际组织与跨法域条约/税制库 (UN / OECD / WTO / WIPO / ICC)

| Source ID | ⁠  资⁠料⁠类⁠型  ⁠ | 来⁠源⁠名⁠称        ⁠ | ⁠ 优⁠先⁠级 ⁠ | 推⁠荐⁠适⁠配⁠器      ⁠ | ⁠ 状⁠态 ⁠ | 备⁠注⁠与⁠实⁠测⁠结⁠论 |
| :--- | :---: | :--- | :---: | :--- | :---: | :--- |
| <a id="dd-088"></a>dd-088 | 通知公告 | [国际商会国际仲裁院（ICC）](https://iccwbo.org/) | P2 | `web` | [ ] | 静态列表与详情链接，需核验详情页正文与附件提取规则。 |
| <a id="dd-145"></a>dd-145 | 其他 | [全球外观设计数据库](https://www3.wipo.int/designdb/en/index.jsp) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-146"></a>dd-146 | 其他 | [PATENTSCOPE数据库](https://patentscope.wipo.int/search/zh/search.jsf) | P2 | `web`（待定位栏目） | [ ] | 站点首页可读，具体业务列表深层栏目 URL 待进一步定位。 |
| <a id="dd-149"></a>dd-149 | 其他 | [WIPOIPPORTAL](https://ipportal.wipo.int/) | P2 | `web`（浏览器渲染/动态接口） | [ ] | 动态渲染或异步接口入口，需配合 CDP 受控浏览器或抓包分析。 |
| <a id="dd-573"></a>dd-573 | 税务资料 | [OECD 国际税收标准与联合国（UN）国际税务合作指南](https://www.oecd.org/en/topics/taxation.html) | P1 | `web` | [ ] | 公开 OECD/UN 税收协定范本、转让定价指南、BEPS 支柱一/支柱二（Pillar 1/2）全球最低税规则。 |
| <a id="dd-574"></a>dd-574 | 法律法规 | [联合国条约系列（UNTS）、WIPO Lex、WTO 法律文本与联合国国际贸易法委员会（UNCITRAL）](https://treaties.un.org/) | P1 | `web` | [ ] | 覆盖多边条约、[WIPO Lex 知识产权立法](https://www.wipo.int/wipolex/)、WTO 争端裁决与 UNCITRAL 国际商事仲裁/破产示范法及判例库（CLOUT）。 |
