# 医学 AI4S 研究工作流

该系统将研究设计、本地数据执行、统计计算、质量审查和证据产物接在同一条流水线上。
模型负责根据公开问题与元数据提出方案；本地运行器只执行预定义的统计方法，
不执行模型编写的任意 Python、R 或 shell。数据库执行仍只有 `src/meddb` 一份实现。

## 安装与无数据验收

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[research]'
.venv/Scripts/python.exe scripts/run_checks.py
.venv/Scripts/python.exe scripts/run_research.py capabilities
.venv/Scripts/python.exe scripts/run_research.py demo --output runs/full-system-demo
```

demo 使用固定随机种子合成数据，运行全部 11 种统计方法，覆盖 MIMIC、SEER8/12/17、
NHANES、BRFSS 的数据集路由。每个方法执行协议校验、数据质控、主分析、预设敏感性分析、
自动审查和报告图表。另运行一个独立合成样本的证据比较。
demo-summary.json 记录各场景状态；这些结果不是医学发现，也不是官方 benchmark 分数。

## 从研究方案执行

```powershell
.venv/Scripts/python.exe scripts/run_research.py study --spec examples/seer-survival.study.json --output runs/seer-study-001
.venv/Scripts/python.exe scripts/run_research.py study --spec examples/seer-survival.study.json --output runs/seer-study-001 --resume
```

示例指向使用者自行准备的本地数据文件，执行前必须修改实际字段、数据版本、定义、
单位及来源。示例不是预先审核过的真实 SEER 研究。
v2 JSON Schema 在 context/schemas/study-v2.schema.json；旧 v1 MIMIC SQL 协议与命令保持兼容。

方案必须包含：研究问题、数据集/版本、分析单位、目标样本或总体、索引事件、时间窗、
分母、暴露窗、随访与删失、目标估计量、数据源、队列规则、方法与变量、缺失值策略、
键/范围/代码/缺失阈值、变量的操作定义与单位/来源/审阅状态，以及预设敏感性分析。

所有分析变量都须有来源定义。预测和因果分析只接受声明为 baseline 的特征；
不能把结局、随访、事件、分组键或时间切分列作为特征。数据集与分析单位必须相容。
跨数据集共用的是协议和方法，不会把不同数据库的患者标识连接起来。

## 统计能力与限制

| 方法 | 实现与诊断 |
| --- | --- |
| describe | 样本量、均值、样本 SD、中位数、四分位数；最多 20 分组 |
| linear / logistic | statsmodels；数值/显式类别编码；HC3 或聚类稳健方差、区间与系数 |
| kaplan_meier | lifelines；右删失、Greenwood 区间、分组生存曲线 |
| cox | lifelines；稳健/患者聚类方差、HR、PH 诊断、concordance |
| competing_risk | Aalen–Johansen；0 删失、1 目标事件、2 竞争事件；累计发生曲线 |
| survey_mean | 分层 PSU Taylor 线性化均值/比例；设计自由度 t 区间 |
| survey_linear / survey_logistic | 加权估计方程 + 分层 PSU Taylor sandwich 方差 |
| predict | 二分类 Logistic；患者分组或时间切分；训练集内插补/标准化/编码；AUC、Brier、校准图 |
| aipw | 连续/二元结局 ATE；患者分组交叉拟合、倾向/结局模型、聚类影响函数区间、重叠诊断 |

复杂抽样方法需要正确的权重、分层、PSU；域分析保留全样本设计 PSU，以零贡献方式
计算域外观测。单 PSU 分层直接拒绝，要求使用者明确处理；不静默套用普通样本方差。
当前 Taylor 方差使用有放回近似，不含有限总体修正、多阶段全部层级或 replicate weights。
跨周期权重调整需按数据来源说明预先完成，系统不会猜测 NHANES 周期权重。

中位数/众数单次插补仅用于允许的方法，不能替代多重插补及其不确定性合并。
预测的插补、标准化和编码只在训练折拟合；时间切分使用严格早于 cutoff 的训练数据，
并剔除与测试患者重合的训练记录。预测结果只报告内部验证，不能冒充外部验证。
AIPW 要求显式声明 exchangeability、positivity、consistency、no_interference；
声明和交叉拟合不能证明无未测混杂。重叠不足和方向不稳定会标记 needs_review。

生存分析不支持延迟入组或时间变化协变量，竞争风险实现为 Aalen–Johansen，
不是 Fine–Gray 回归。各方法支持范围由 capabilities 命令和 v2 协议明确列出。

## 数据源、只读执行与本地产物

data.kind=file 支持本地 CSV 或 records JSON，路径相对方案文件解析，限制 128 MiB /
一百万行。文件应由使用者按权限准备，禁止把临床行或数据文件送给模型。
data.kind=postgres 要求 config、SQL、params、output_columns；验证 AST、schema、catalog、
只读用户与事务后，调用 MedDB detail=True 在本地取数。它不会在终端打印临床行。
查询仍受配置的 detail_max_rows、执行时间和计划成本限制，截断即拒绝分析。
默认明细上限很小，正式本地分析需在自己的 config/*.local.json 中明确设置所需上限。
系统不会自动改账户、权限、认证或扩大这些限制。

运行目录内会保存 **source.local.json（可能含患者级本地数据）**，用于冻结分析样本和恢复。
这些目录只能放仓库 Git-ignored 且未跟踪的 runs/、artifacts/，或明确指定的仓库外本地目录。
不要上传、分享或打包完整运行目录。分享报告前同样应审查小样本统计及来源限制。
本地快照按 pandas table JSON 保存；首次分析与恢复均读取同一份快照，固定其数值表示。

其它产物为 study.json、intake.json、quality.json、definitions.json、analysis.json、
sensitivity.json、review.json、state.json、report.md/report.html、analysis.png/analysis.svg。
报告为确定性的统计证据说明，每个结果指向主分析、协议和运行指纹，不编造文献或因果结论。
图表是可单独导出的静态 PNG/SVG，不依赖在线服务。

质控失败的状态是 blocked_quality，不执行统计方法；计算失败是 failed，记录阶段与错误类型，
不回显可能包含患者值的异常正文。未审阅定义、计算警告、PH 诊断、重叠不足、敏感性失败等
状态为 needs_review。completed 表示预定义技术检查通过，始终不表示科学有效性已获认证。

## 文献与定义来源

```powershell
.venv/Scripts/python.exe scripts/run_research.py literature --query 'sepsis phenotype validation' --limit 5 --output runs/literature.json
.venv/Scripts/python.exe scripts/run_research.py verify-evidence --spec my-study.json --output runs/source-verification.json
```

只有这两个显式命令访问公开 PubMed 元数据。literature 检索真实 PMID、标题与年份；
verify-evidence 通过 NCBI 查询核对 PMID/标题；本地 study 默认不联网，只核对来源引用、
HTTPS/PMID 链接格式及人工审阅声明。文献被检索到不代表它验证了某个临床定义。
临床定义需按变量逐条声明操作口径、适用时间/单位和来源，人工审阅标记不能由模型擅自升级。

## Agent 规划与多轮修订

brief 是公开研究元数据：study_id、question、dataset、analysis_unit、population、design、
data、cohort、quality、definitions、evidence，另可提供 method_hint、schema_context、sensitivity。
可从完整 v2 方案去掉 schema_version 和 analysis 得到起点。
schema_context 仅允许放公开字段定义，不能放患者数据或 Gold。

```powershell
.venv/Scripts/python.exe scripts/run_research.py study-plan --brief brief.json --model YOUR_MODEL --output runs/planned-study.json
.venv/Scripts/python.exe scripts/run_research.py agent --brief brief.json --model YOUR_MODEL --output runs/agent-study --max-attempts 2
.venv/Scripts/python.exe scripts/run_research.py agent --brief brief.json --model YOUR_MODEL --output runs/agent-study --max-attempts 2 --resume
.venv/Scripts/python.exe scripts/run_research.py revise --spec revised-study.json --session runs/research-session --note 'Clarify the baseline exposure window before analysis'
```

模型只收到公开问题、元数据和定义，不收到本地路径、配置、观察记录或模型结果。
PostgreSQL source 未提供 SQL 时，模型可依据公开 schema 生成本地取数 SQL；提供 SQL 时绑定原源。
agent 具有最多三次、默认两次的规划尝试预算，完成合法方案后创建版本记录并执行完整本地流程。
不会根据 p 值自动换假设、反复改队列或把分析结果发回模型。
revise 保留最多 20 个带父版本指纹的不可变方案；旧版本被修改时拒绝继续写入。
新的研究设计应先修订方案，再用独立运行目录执行，避免把事后探索伪装为预设分析。

## 跨数据集证据比较

```powershell
.venv/Scripts/python.exe scripts/run_research.py compare --runs runs/study-A runs/study-B --output runs/evidence-comparison
```

比较前核对每个运行的产物指纹和完成状态。按方法、估计量、分析单位、目标总体、
索引事件/时间窗/分母/随访以及临床概念定义检查声明的一致性；不相容时明确标记。
结果保留各数据集估计值，始终不自动合并效应量，不宣称不同研究就构成外部验证。

## 与 benchmark 的关系

CLINSQL validation 继续使用单独的旧 v1 聚合 SQL 流程，Gold 只由评分器读取。
上述统计工作流不依赖 Gold，不改变官方封存 test 或 legacy 评测。
数据库、PubMed 和模型真实联调需要外部配置；离线验收与合成研究结果应分别报告。
