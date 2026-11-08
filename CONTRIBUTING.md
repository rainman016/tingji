# 参与开发

主要支持 Windows x64 / Python 3.12。界面使用 CustomTkinter，复用现有按钮、阅读器和工作区，不需要 Node.js。

## 开发与测试

先运行 `python -m pip install -r requirements.txt numpy` 安装测试依赖，再运行 `python -B -m unittest discover -s tests -v`。完整本机回归使用已安装的项目环境。普通测试使用合成材料和模拟引擎，不下载模型或运行长视频。

GitHub Actions 当前尚未启用，已提供 [Windows 回归模板](docs/ci/windows-regression.yml)。维护者可在 GitHub 网页创建 `.github/workflows/tests.yml` 并复制模板内容，或使用具备工作流写入权限的 Git 客户端提交该文件。启用后会在推送、拉取请求和手动触发时运行普通回归；真实模型测试仍需显式运行。

`tests/render_workspace.py` 创建独立合成文稿窗口并捕获该窗口，检查宽窄布局。真实模型测试为显式入口：`tests/validate_summary_recovery.py stop <文稿.json> <输出目录>`，结束后再运行相同参数的 `resume`。这会占用本机 GPU，并将测试副本写入输出目录；请不要将私人文稿提交到仓库。

## 代码入口

- `app.py` / `desktop_ui.py`：桌面任务与视觉层；`workspace_actions.py` / `workspace_view.py`：阅读、校对和响应式布局。
- `local_service.py`：本地任务、持久化、进程调度；`local_worker.py`：摘要和识别 worker。
- `checkpoint_store.py` / `transcription_recovery.py`：识别恢复与进程锁。
- `summary_state.py` / `summary_resume.py`：摘要版本快照和逐步恢复；`summary_format.py`：结构校验与有界重试；`summary_validation.py`：引用核对。
- `summary_backend.py`：CPU / Vulkan 引擎隔离与实际卸载验证。

请为数据保存、取消、恢复和跨进程逻辑补充必要测试。修改视觉层后检查 1240×820、960×680、键盘操作与错误状态。提交中不要包含模型、录音、文稿、凭据、日志或本地运行目录。

提交问题时请附 Windows/Python 版本、操作步骤和去敏后的错误信息；模型推理的测试结果应与模拟测试分开说明。
