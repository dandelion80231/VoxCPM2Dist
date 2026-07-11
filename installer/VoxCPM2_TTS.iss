#define MyAppName "VoxCPM2 TTS 中文版"
#define MyAppVersion "5.0"
#define MyPayload "D:\AI\Build\VoxCPM2Dist\payload"
#define MyAssets "D:\AI\Build\VoxCPM2Dist\installer\assets"

[Setup]
AppId={{VoxCPM2-TTS-ZH-5.0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=VoxCPM2
DefaultDirName={autopf}\VoxCPM2 TTS
DefaultGroupName={#MyAppName}
OutputDir=D:\AI\Build\VoxCPM2Dist\output
OutputBaseFilename=VoxCPM2_TTS_v5.0_Setup
Compression=lzma2/fast
SolidCompression=no
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\VoxCPM_App.ico
DirExistsWarning=no
DisableDirPage=no
DiskSpanning=yes
DiskSliceSize=2000000000

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
; 预压缩的 app 归档（7z 多线程解压，比 InnoSetup 原生 LZMA 快 2-3x）
Source: "{#MyPayload}\app.7z"; DestDir: "{app}"; Flags: nocompression
; 7z 独立解压器（安装时解压 app.7z 后自动清理）
Source: "{#MyPayload}\7za.exe"; DestDir: "{app}"; Flags: nocompression
; 应用图标
Source: "{#MyAssets}\VoxCPM_App.ico"; DestDir: "{app}"; Flags: nocompression

[Icons]
; 注意：冻结的 .exe 已不再随包发布（原 PyInstaller .spec 已遗失）。
; 现改用随包 python_cuda 直接运行 .py 入口，由以下 .bat 启动器拉起，
; 既保留双击图标体验，又无需重新冻结，且天然包含新增的降噪依赖与模型。
Name: "{group}\{#MyAppName} - 交互菜单"; Filename: "{app}\Scripts\Launch_TTS_Menu.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Comment: "交互式菜单界面（控制台）"
Name: "{group}\{#MyAppName} - 网页界面"; Filename: "{app}\start_web_ui.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Comment: "浏览器图形界面"
Name: "{autodesktop}\{#MyAppName} - 交互菜单"; Filename: "{app}\Scripts\Launch_TTS_Menu.bat"; IconFilename: "{app}\VoxCPM_App.ico"
Name: "{autodesktop}\{#MyAppName} - 网页界面"; Filename: "{app}\start_web_ui.bat"; IconFilename: "{app}\VoxCPM_App.ico"

[Run]
; 安装后解压 app.7z 到 {app}（7z 多线程解压，速度快）
Filename: "{app}\7za.exe"; Parameters: "x ""{app}\app.7z"" -o""{app}"" -y"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated
; 清理归档与解压器
Filename: "cmd.exe"; Parameters: "/c del /q ""{app}\app.7z"" ""{app}\7za.exe"""; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
