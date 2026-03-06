# Mira iOS — Xcode 项目设置

## 1. 创建项目

1. Xcode → File → New → Project
2. iOS → App
3. Product Name: `Mira`
4. Team: 你的开发者账号（Personal Team 也行）
5. Organization Identifier: `com.[yourname]`
6. Interface: **SwiftUI**
7. Language: **Swift**
8. Storage: None
9. 保存到 `Mira-iOS/` 目录

## 2. 替换文件

把 `Mira/` 下的 Swift 文件拖入 Xcode 项目，替换自动生成的文件：
- 删除自动生成的 `ContentView.swift`
- 用我们的 `MiraApp.swift` 替换自动生成的

## 3. 配置 Entitlements

方法 A（推荐）：
1. 选中项目 → Signing & Capabilities → + Capability
2. 搜不到 iCloud 没关系，这个 app 不需要 iCloud capability
3. 它直接通过 .fileImporter 访问用户选择的文件夹（document picker）

方法 B：
- 直接用项目里的 `Mira.entitlements`

## 4. Info.plist

在 Info tab 添加：
- `Supports Document Browser` = YES（如果需要）

注意：app 通过 `.fileImporter` (UIDocumentPickerViewController) 访问 iCloud Drive，
不需要 iCloud entitlement。用户选择文件夹后，app 保存 security-scoped bookmark。

## 5. Deployment Target

- iOS 17.0+（用了 @Observable）

## 6. Mac 端准备

在 Mac 上创建 Mira 文件夹：
```bash
mkdir -p ~/Library/Mobile\ Documents/com~apple~CloudDocs/Mira
```

这会在 iCloud Drive 中创建 `Mira` 文件夹。

如果要给家人用：
1. 打开 Finder → iCloud Drive
2. 右键 `Mira` 文件夹 → 共享
3. 添加家人的 Apple ID

## 7. 测试

1. iPhone 上运行 app
2. 首次打开会要求选择文件夹 → 导航到 iCloud Drive → Mira
3. 发一条消息
4. 在 Mac 上检查 `~/Library/Mobile Documents/com~apple~CloudDocs/Mira/inbox/`
5. 手动跑 `python3 core.py talk` 测试处理
6. 回到 iPhone app，下拉刷新看回复
