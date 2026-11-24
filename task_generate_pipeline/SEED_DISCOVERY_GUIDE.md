# 缺少种子时如何处理

正常运行 `pipeline.py plan` 时，已有分组绑定保持不变。对于没有候选的完整数据分组，管线会自动读取三个来源的实际开源题面：

- TB-Science 的 `tasks/**/instruction.md`，并固定 Git 提交。
- MLE-bench 的 `mlebench/competitions/*/description.md`，并固定 Git 提交。
- NatureBench 数据集的 `tasks/<case_id>/problem/README.md`，使用固定的数据集版本。

题面缓存、索引和来源下载失败记录保存在 `--seed-cache` 指定的目录。没有指定时，放在本次计划旁的 `benchmark-cache/`。缓存中的文件会核对 SHA256。匹配阶段也会保留所选题目的原始任务配置或元数据。

模型先读取数据源的实际格式证据，再在三个来源的题面中选择一个能迁移核心考核方式的种子。排序仅帮助检索，模型可以读取完整索引和其他题面。未实际读取所选原始题面的选择不会进入种子库。

选中后，将原始题面、版本、哈希、分组、必要数据条件和适配理由追加到 `seed_catalog.json`，并把该组固定到同一份种子。后续任务规划、构包和验收会收到原始题面证据。已有种子记录和分组绑定不会被这一步替换。

一个数据源生成一个任务包；同组所有数据源共享种子，不表示一个包必须包含整组数据。模型的评测要求按单份来源编写，来源自身有多个必要文件时仍需齐全。历史契约确需澄清时，必须备份原种子库、记录旧新哈希及绑定迁移，并使用新的运行目录，不能静默覆盖旧运行证据。

匹配和任务规划也会收到实际后端能力：当前评估提交的结果文件，不执行 Solver 提交的程序。不能把原本交数值或预测结果的题目额外改成必须提交代码并验证重新生成结果。Solver 在解题时使用代码不受这条交付约定限制。原题确实考核程序执行行为时，应报告缺少相应后端，不能用语法检查冒充运行验证。

没有合适题面、来源无法读取、数据本身不支持目标，都会报告具体原因。没有候选的分组不会被随意绑定到不相干题目。

## 运行

```bash
python pipeline.py plan \
  --inventory inventory.csv \
  --seeds seed_catalog.json \
  --bindings runs/new-batch/groups.json \
  --output runs/new-batch/plan.json \
  --data-root /path/to/read-only/data \
  --seed-cache runs/new-batch/benchmark-cache \
  --account-pool /path/to/private/accounts.json \
  --model MODEL --effort medium
```

只有参考资料下载需要代理时，可加 `--discovery-proxy http://127.0.0.1:PORT`。代理需要认证时，使用 `--discovery-proxy-file /path/to/private/proxy-url`，从私有文件读取完整地址，避免在命令参数中暴露凭据。它只影响本次参考检索，不改全服务器代理。Codex 调用继续使用已有账号池与启动器。

每组模型完成选择后会保存 `selection.json`。如果随后下载原始配置失败，重试可以复用这次选择；源证据、上游题面或提示词变化时，旧选择自动失效。检索未匹配的具体原因也会写入每条数据的计划结果。

`--source-profiles profiles.json` 可提供受控预检得到的格式和资源证据。没有它时，会尝试从只读数据路径获取有限的格式证据。完整性、科学依据和实际运行仍由后续构包与独立验收检查。

`--no-seed-discovery` 只使用已有种子库。`--preview` 保持离线，不访问模型、原始数据或网络，也不写分组绑定。

## 下载状态的含义

`部分文件已完成` 是原始清单中的状态。目前计划入口仍按清单的完整下载标记筛选。该状态本身不能证明已下载的子集不够构包。它需要独立的数据可用性判断，本次缺种子检索不会修改原 Excel 或把下载状态改成完成。
