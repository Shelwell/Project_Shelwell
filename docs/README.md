# docs 说明

> 跨模块详细文档。模块内部接口/参数放各目录 `README.md`；这里放设计、评审、规范和部署。

## 1. 文档索引

| 文档 | 内容 | 状态 |
|---|---|---|
| [memory_design.md](memory_design.md) | 记忆系统双层设计：情景/语义、巩固、遗忘、检索 | 设计稿，部分已落地 |
| [memory_implementation_review.md](memory_implementation_review.md) | 记忆实现方案与参数建议、分阶段落地 | 评审稿 |
| [CONVENTIONS.md](CONVENTIONS.md) | 编码、环境、命名、配置、测试规范 | 现行 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 服务化部署、端口、NapCat、日志、分发 | 现行 |

当前记忆实现以 [../backend/memory/README.md](../backend/memory/README.md) 为准；设计稿用于解释“为什么这样分层”，不一致时以代码和模块 README 为准。

## 2. 阅读顺序

1. 根目录 [ARCHITECTURE.md](../ARCHITECTURE.md)：全局定位/分层
2. [文件树.md](../文件树.md)：找文件
3. 对应目录 `README.md`：接口/参数/实现
4. 本目录设计/部署文档：跨模块背景

## 3. 文档规则

- **一个事实只写一处**：参数写在 `config/README.md`，接口写在模块 README，设计写在 `docs/`
- 模块 README 只链接 `docs/`，不复制长文
- 设计稿落地后，更新状态或标注“已实现部分”，避免把设计当现状
- 新增跨模块能力：在 `docs/` 建文档，并在本索引加一行
