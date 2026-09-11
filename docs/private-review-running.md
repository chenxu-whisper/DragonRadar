# 私人复盘本机运行说明

状态：第三批编码中，以下命令是本批预期运行入口；最终验收结果另行记录。
所有命令在 `.worktrees/private-review` 执行，不在前两批工作树执行。

## 独立启动

```powershell
.\scripts\start-private.ps1
```

默认入口 `https://localhost:8030`。首次可传 `-Port` 选择空闲端口，
该端口写入独立实例配置；后续启动不隐式改变已绑定会话的 Origin。
使用独立 Compose 项目 `dragon-radar-private-review`，只映射回环 HTTPS 端口。
API、数据库不发布宿主端口，不启用隧道或公网分享。

脚本构建前后端、迁移、初始化稳定本机主体、等待服务健康，并导出本项目 CA 公钥。
不会导入虚构或真实行情。第一次使用仍是空库，已有私人记录则从独立数据库恢复。

`.temp/private-secrets` 在写凭据前限制 ACL 为当前 Windows 用户和 SYSTEM。
保留数据库卷时也需保留 `.temp/private-secrets/compose.env`，
不要把该文件移到前端目录、提交到 Git 或发送到聊天。
容器内文件权限不是磁盘加密；本机系统管理员和 Docker 管理员仍有访问能力。

## HTTPS 信任

```powershell
.\scripts\private-owner.ps1 -Action Certificate
```

该命令只显示本项目 CA 公钥文件路径与文件 SHA256，不修改信任库。
证书位于 `.temp/private-secrets/localhost-root.crt`，私钥不会导出。
应先核对公钥证书、指纹与用途，再单独批准当前用户信任库变更。
不忽略浏览器证书警告、不禁用 TLS 验证、不使用 HTTP 输入私人信息。

获准后由本机用户显式执行：

```powershell
$certificate = Resolve-Path .temp/private-secrets/localhost-root.crt
certutil -user -addstore Root $certificate
```

需要撤销信任时，先用 `certutil -dump` 查看该证书的 SHA1 指纹，
然后只删除对应证书，禁止按名称批量删除其他应用证书。

```powershell
certutil -dump .temp/private-secrets/localhost-root.crt
certutil -user -delstore Root "<核对后的本项目证书SHA1指纹>"
```

未完成本步骤前，服务的内部 TLS 健康检查可通过，但不代表浏览器已信任此证书。

## 配对与撤销

确认浏览器信任后，用本机 CLI 签发一次性配对码：

```powershell
.\scripts\private-owner.ps1 -Action Pair
```

配对码只写入受保护的本机文件，在 5 分钟内于上述 HTTPS 入口提交；
不会自动把码放入 URL 或剪贴板。新签发使旧的未使用配对码失效。
会话期限为 8 小时，到期需重新配对；本批无需注册账号或配置第三方登录。

```powershell
.\scripts\private-owner.ps1 -Action Revoke
```

撤销会话和待用配对码不删除已保存笔记、自选或快照。
页面收到失效结果后隐藏私人内容，未保存草稿仅在当前页面内存中锁定；
同主体重新配对后可恢复。显式退出、关闭或刷新页面可能丢弃未保存输入。

## 停止与保留

```powershell
docker compose -p dragon-radar-private-review --env-file .temp/private-secrets/compose.env -f compose.yaml -f compose.private.yaml stop
```

不要使用 `down -v` 更新或回退。数据库卷不是备份；CA 私钥卷也需按本机安全要求保护。
当前不承诺完整备份恢复目标或公网可用性，真实来源授权必须另行完成。
