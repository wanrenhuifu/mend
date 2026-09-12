# Mend

> **一个会自己跑测试证明改对了的 coding agent —— 不重写，只修补。**

Mend is a coding agent that repairs an existing codebase: it reads the repo, makes the smallest correct change, then **proves the fix by running the tests**.

「改好了」不算完成，**测试从红变绿才算完成**。

---

## 它做什么

```bash
mend run "登录接口在超时后没有重试，修一下"
```

1. **看地图**：先给模型一份仓库摘要（目录 + 文件名 + 行数），再按任务关键词初筛出少数候选文件 —— 不是把整个仓库塞进上下文。
2. **精确改**：用 `edit_file` 做「唯一匹配」的字符串替换，产出尽可能小的 diff，而不是重写整个文件。
3. **跑测试**：改完进入验证门。测试不绿就不允许结束，报错原文回灌给它继续改；连续两次同样的失败会提示它换思路。仓库里没有可跑的测试时，它不会假装完成，而是明确告诉你「无法验证」。
4. **留轨迹**：每一步（读了什么、改了什么、测试输出是什么）都写进 `.mend/runs/*.jsonl`，可以完整回放。

## 30 秒跑起来

```bash
python -m mend doctor                      # 环境自检
python -m mend eval --fake                 # 离线跑通完整闭环（不需要密钥、不需要网络）
python -m mend plan --fake "看看这个仓库"    # 看 agent 的上下文、可用工具和轨迹
```

核心零依赖，只要 Python >= 3.11 就能跑。接上真实模型：

```bash
export MEND_API_KEY=sk-xxx                 # Windows PowerShell: $env:MEND_API_KEY="sk-xxx"
python -m mend run "把用户列表接口的分页参数从 page 改成 offset"
```

想直接用 `mend` 命令（而不是 `python -m mend`）：`pip install -e ".[dev]"`。

## CLI

| 命令 | 作用 | 工具权限 |
|---|---|---|
| `mend plan "任务"` | 只读：输出上下文 + 可用工具 + 计划，不改代码 | read |
| `mend run "任务"` | 完整闭环：读 -> 改 -> 跑测试 -> 迭代 | read / write / shell |
| `mend fix` | 把当前失败的测试修到通过 | read / write / shell |
| `mend review` | 审查当前 diff，只给意见 | read |
| `mend eval` | 跑评测集：通过率 / 步数 / 耗时 | read / write / shell |
| `mend trace [id]` | 列出或回放历史运行轨迹 | - |
| `mend doctor` | 环境自检 | - |
| `mend init` | 生成 mend.toml | - |

## 一次运行发生了什么

```
任务
 ├─ context  仓库地图 + 关键词初筛         context.py
 ├─ llm      模型决定下一步                 llm.py
 ├─ tool     执行工具调用，结果回灌给模型    tools.py
 │    read    list_dir / read_file / search / git_status / git_diff
 │    write   edit_file / write_file            <- 只在 run / fix 里挂载
 │    shell   run_command（白名单 + 超时）       <- 只在 run / fix 里挂载
 ├─ verify   声称完成就必须跑测试            verify.py
 │    通过 -> 结束；失败 -> 回灌报错，回到 llm
 └─ stop     输出说明 + diff + 轨迹
```

## 设计取舍（面试会问的五个问题）

**1. 为什么不用 LangChain？**
核心只有三件事：组装上下文、调度工具、判断什么时候算完。自己写（这个仓库 1500 行左右）才能在面试里讲清每一步的失败模式，也才能让别人 30 秒复现。框架替你省掉的部分，往往正是 agent 最难的部分。

**2. 怎么防止「改测试让测试变绿」？**
目前是提示词明确禁止 + 轨迹留痕（review 时能发现）。这还不够硬 —— 下一步是在工具层把测试文件设成不可写，做成结构保证。这条列在 Roadmap 里，也写在 [docs/design.md](docs/design.md) 的「已知弱点」。

**3. 只读模式怎么保证？**
不是靠提示词求模型别乱改，而是工具分级挂载：`plan` / `review` 只挂 read 级工具，写工具既不在模型的 schema 里，真调了也会被 `registry.call()` 挡掉。

**4. 上下文怎么选？**
先给地图（每个文件多少行），再按任务关键词打分初筛（英文按单词切，中文用 2-gram，因为没有词边界），读文件默认只给 400 行。上下文长度 = 成本 = 延迟，也是 agent 最常见的失败来源。

**5. 为什么零依赖？**
标准库够用：`urllib` 调 API、`argparse` 做 CLI、`tomllib` 读配置。面试官 `git clone` 之后不建虚拟环境就能跑，这比多几个漂亮的依赖值钱。

## 评测

```bash
python -m mend eval --fake      # 离线：用任务自带剧本验证整条流水线
python -m mend eval --json      # 接上真实模型后：输出可统计的结果
```

一个任务 = 一个坏仓库（`evals/fixtures/`）+ 一句任务描述 + 一条判定命令。评测时 fixture 会被复制到临时目录并 `git init`，**你的原仓库全程不动**。目前带了一个示例任务 `off_by_one`。

## 配置

`mend init` 生成 `mend.toml`（带注释的完整版本见 [mend.example.toml](mend.example.toml)）。密钥只从环境变量或 `.env` 读，永远不写进配置文件。

## 目录结构

```
mend/
├── cli.py        # 8 个子命令
├── agent.py      # 主循环：验证门 / 防打转 / 步数预算
├── tools.py      # 工具层：路径安全 + 三级权限 + 命令白名单
├── context.py    # 仓库地图 + 关键词初筛
├── verify.py     # 跑测试并判定
├── llm.py        # LLM 抽象 + OpenAI 兼容实现 + 离线假模型
├── trace.py      # 轨迹落盘与回放
├── evaluator.py  # 评测集
├── config.py     # 配置：默认值 <- mend.toml <- 环境变量
└── vcs.py        # git 封装
```

## Roadmap

- [ ] 测试文件不可写（结构上防止改测试作弊）
- [ ] 多文件改动 + 可回滚的事务
- [ ] 用 tree-sitter 做符号索引，替代关键词初筛
- [ ] 按 diff 指纹缓存验证结果，避免重复跑测试
- [ ] 评测集扩到 10+ 个任务，输出成功率 / 步数 / 成本曲线

## License

MIT
