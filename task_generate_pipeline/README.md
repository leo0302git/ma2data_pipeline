# task_generate_pipeline

从下载清单和冻结的 benchmark 种子生成 NatureGym 风格的任务包。Agent 编写数据准备程序、题面、Dockerfile 和任务专用评分逻辑。程序负责核对来源、隔离执行和保存验收证据。独立验收通过的包才进入 `passed/`。

当前源码版本为 **v32（2026-11-24 维护修复）**。保留上游 `metadata_guide.md`、`level3_rules.md`、`level2_rules.md` 的原始文件。默认比较口径为 `strict_upstream_reported`；本项目的新任务使用明确指定的 `--comparison-profile generated_measured`，要求真实强弱参考实跑，不能宣称文献 SOTA。缺对应证据时保留候选和原因。

v32 统一了项目与运行目录的暂停检查，补齐严格 JSON 的一次格式反馈，并增加不调用模型的 `status`、`audit` 和 `preflight`。完整操作见 [运行维护说明](OPERATIONS.md)。旧版本、旧任务及其验收签名继续保留；本版维护测试不代表旧任务已按新代码重新构包。

旧批次的历史 `pass` 报告不会被改写。查阅这些批次时应另看比较证据审计，分别确认构包/运行验证和严格比较证据状态。

## 先看清单可以匹配到哪些组

在项目根目录用 Python 3.11 或更高版本执行。`--preview` 只读 CSV 和种子库，适合先查看现有覆盖。

```powershell
python task_generate_pipeline/pipeline.py plan --preview --output outputs/agent-v2/preview.json
```

CSV 沿用 Excel 的本体学科和本体任务。`seed_catalog.json` 保存已有候选和后续检索追加的种子；预览只检查现有覆盖。正式计划会为缺候选的完整数据分组自动检索 NatureBench、TB-Science 和 MLE-bench 的原始题面。同一个组一旦选定种子，后续任务沿用相同 ID 和规格摘要。

部署项目由 `.pipeline-project.json` 指定共用的 `config/seed-registry/`，种子库和分组绑定按原内容迁入，换引擎版本或输出目录都继续使用它。单独解压且未注册的源码使用随附种子库和绑定。保留原映射，不要每批新建空映射；注册文件丢失会报错。只有独立实验才显式传另一个 `--bindings`。修改已冻结的分组种子需要明确迁移并保存旧映射。

种子记录里的文件后缀只提供检索线索，不是构包白名单。同组出现新格式时仍沿用冻结种子，再由模型检查内容是否满足题型；缺观测、标签或模态不符依然会拦截。

## 用自己的单账号号池选择种子

```powershell
$pool = "$env:LOCALAPPDATA/Frontis/task_generate_pipeline/accounts.json"
python task_generate_pipeline/pipeline.py plan --output outputs/agent-v2/plan.json --account-pool $pool --model gpt-6.1-sol
```

每个未绑定的组执行一次种子选择阶段。已有候选时从库中选择；没有候选时，Agent 先读取源数据格式证据和三个 benchmark 的实际题面，再决定能否迁移核心考核方式。选择结果包含原始版本、哈希、最低数据要求和适配理由。缺合适题面或源证据不足会保留具体原因。新组的结果保存后，构包才能读取该计划。缓存和代理选项见[种子检索说明](SEED_DISCOVERY_GUIDE.md)。

## 正式构包和验收

准备可运行 Linux 容器的本机 Docker 和数据根目录。部署时复制整个项目目录，包含 [vendor/naturegym](vendor/naturegym/README.md) 下的 `.agents/` 和 `scripts/`。管线会校验随项目分发的 NatureGym 文件，并从中加载原生评估器和静态检查器。H5AD 的宿主画像可选装 `h5py`。下面的可信基础镜像供常见科学 Python 题使用。其他工具链可以提供预装并经人工批准的本地镜像。

```powershell
docker build -t task-generate/science:1 -f task_generate_pipeline/runtime-images/Dockerfile.science task_generate_pipeline/runtime-images
python task_generate_pipeline/pipeline.py run --plan outputs/agent-v2/plan.json --data-root 'D:/实际数据根目录' --run-dir outputs/agent-v2/run1 --base-image task-generate/science:1 --source-id 'cellxgene:fb83600a-7823-426e-b06b-1fcd666a3cd8' --account-pool $pool --model gpt-6.1-sol
```

本地数据目录下保留清单里的 `raw/http/...` 层级。路径来自另一个服务器根时，补 `--server-prefix`。默认串行，适合单账号。试一批时可用 `--limit 3`，或重复传 `--source-id`。中断后用同一条命令加 `--resume`，会复用已冻结的方案、已写好的草稿及与当前包和镜像一致的实跑证据。改了源文件、种子、提示词、代码或运行参数时，使用新的运行目录。

## 看输出

- `batch-report.json` 汇总每个来源的状态。
- `cases/<任务ID>/` 保存冻结来源、PTB 式改造方案、阶段日志和各轮尝试。
- `cases/<任务ID>/attempt-*/task/` 是 Agent 写出的候选包。`input/` 给 Solver，`evaluation/` 和私有清单由验收侧保管。
- `cases/<任务ID>/attempt-*/task_verify_result.json` 保存 NatureGym 36 项核查和动态证据。原始执行报告留在本地运行目录。
- `verification-preparation-failed-*/` 保留独立测试准备程序的失败日志与源码。此类执行错误可由独立测试 Agent 单独修复一次；科学检查失败不会被改成程序错误或通过。
- `passed/<任务ID>/` 只保存完整通过的包。模型等待、缺运行条件或缺证据都不会发布。

这一版的执行后端覆盖离线 CPU 的文件交付任务，包括程序评分和规则 rubric。需要执行 Solver 提交的程序、可信 sidecar 或真实 LLM Judge 的任务，会在对应的执行后端具备之前保留为 `not_verified`。Windows 上的真实 Docker 构包和验收需安装可用的 Docker。

各阶段的设计和对应文件见[构建说明](BUILD_GUIDE.md)。


## 新任务实测比较模式

本项目已选择新任务实测口径。构包命令 `pipeline.py run` 需明确增加 `--comparison-profile generated_measured`；使用新的运行目录。缺省仍是原始已报道参考口径。完整说明见 [COMPARISON_PROFILES.md](COMPARISON_PROFILES.md)。实测通过不代表文献 SOTA 或严格原始报道规则认证。

v23 补充了 Scope 输出格式的有界反馈：字段超过 4000 字符时，告知模型具体字段和长度，最多重新修正一次；不会由脚本截掉科学约束，也不会重试模型明确报告的真实数据缺口。


v24 修复独立审查的上下文传递：保留最近请求的完整代码，旧日志按需读取；完整审查即便有 failed/not_verified 也输出全部检查结论。该修复不改变科学目标、约束、指标和判分器。


v25 补齐了独立验证模型对逐控制样例原生报告、执行回执和私有参考方法源码的只读访问；仍不开放账号文件或任意提交材料。完整日志按需读取，不依赖截断摘要完成验收。
