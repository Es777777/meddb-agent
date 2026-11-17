# 发布检查

公开代码仓库：`https://github.com/Es777777/meddb-agent`，默认分支 main。
用户已明确授权公开发布；现有 LICENSE_NOTICE.md 保留未指定开源许可证的状态。
后续发布前：

1. 确认作者身份、仓库名与许可，选择公开还是私有。
2. 运行离线检查，阅读未移植的历史路径说明。
3. 执行 `git status --short`，逐个审查待加入文件；不得加入数据、catalog、结果、Gold、凭据、环境或旧 Git 历史。
4. 确认目标 remote 和分支后提交、推送，不能将本地完整工作目录作为源码附件公开上传。

本地通过不等于 GitHub Actions 已成功；Actions 必须在上传后真实运行才能确认。
