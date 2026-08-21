# 第一次把股票扫描器上传到 GitHub

这份指南不需要命令行，也不需要一直打开 Codex。

## 1. 注册并登录

打开 <https://github.com/signup>，注册账号并完成邮箱验证。

## 2. 新建仓库

1. 登录后，点击页面右上角的 `+`。
2. 选择 `New repository`。
3. `Repository name` 填写 `us-stock-scanner`。
4. `Public` 和 `Private` 都可以；第一次使用可选择 `Private`。
5. 不要勾选自动添加 README、`.gitignore` 或 License。
6. 点击 `Create repository`。

## 3. 找到上传入口

如果仓库页面已经有文件：

1. 回到仓库首页，也就是顶部 `Code` 标签页。
2. 在文件列表右上方点击 `Add file`。
3. 选择 `Upload files`。

如果仓库完全为空，页面的 `Quick setup` 区域也可能显示 `uploading an existing file`。找不到这句话时，使用 `Add file` → `Upload files` 即可。

GitHub 官方上传说明：<https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository>

## 4. 上传正确的内容

1. 先在电脑上解压 `github-stock-scanner.zip`。
2. 打开解压后的文件夹。
3. 将文件夹里面的全部内容拖进 GitHub 的 `Upload files` 页面。
4. 确认上传列表中包含：
   - `.github/workflows/daily_scan.yml`
   - `stock_scanner` 文件夹
   - `scanner.py`
   - `requirements.txt`
   - `README.md`
   - `reports` 文件夹
5. 页面底部的提交说明可填写 `Initial stock scanner`。
6. 点击 `Commit changes`。如果页面显示 `Propose changes`，也可以点击并按提示完成。

不要只上传 ZIP 文件本身；GitHub Actions 需要看到解压后的 `.github` 和 Python 文件。

## 5. 第一次手动运行

1. 打开仓库顶部的 `Actions`。
2. 如果 GitHub 要求确认启用工作流，点击启用。
3. 左侧选择 `Daily US stock scan`。
4. 点击右侧 `Run workflow`，再点击绿色的 `Run workflow`。
5. 等待运行记录变成绿色对勾。

运行成功后，真实结果会出现在 `reports/latest.csv` 和 `reports/latest.md`。运行页面下方也会提供可下载的报告压缩包。

## 6. 自动运行

工作流会在每周一至周五美股正常收盘后自动运行。你不需要保持电脑开机，也不需要保持 Codex 打开。

如果自动提交报告失败，请进入仓库：

`Settings` → `Actions` → `General` → `Workflow permissions`

选择 `Read and write permissions` 并保存。即使自动提交失败，Actions 运行页面里的报告附件通常仍然可以下载。
