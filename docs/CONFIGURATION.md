# 配置与数据库依赖

config/meddb.json、meddb-mimic-local.json、meddb-mimic-official.json 是无密码示例。使用前核对 host、port、dbname、只读 user、允许 schema 和本地 Codex 配置路径。不要提交本机认证文件。

src 布局下 PROJECT_ROOT 解析到仓库根目录；catalog、templates、exports、audit 的相对路径均以仓库为基准。缺少 schema catalog 时，元数据和部分模板命令不可用。真实 catalog 应在使用者自己的数据库上重建，不随仓库发布。

准备完成后，可在普通终端显式运行 `python scripts/run_meddb.py --config config/meddb-mimic-local.json status`。这会连接数据库，不属于离线 CI。本次整理不执行该命令，不创建数据库角色、不修改认证策略。
