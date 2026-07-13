#define MyAppName "VoxCPM2 TTS 中文版"
#define MyAppVersion "5.2"
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
OutputBaseFilename=VoxCPM2_TTS_v5.2_Setup
Compression=lzma2/fast
SolidCompression=no
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\VoxCPM_App.ico
DirExistsWarning=no
DisableDirPage=no
DiskSpanning=yes
DiskSliceSize=2000000000

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"

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
Name: "{autodesktop}\{#MyAppName} - 交互菜单"; Filename: "{app}\Scripts\Launch_TTS_Menu.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Tasks: desktopicon
Name: "{autodesktop}\{#MyAppName} - 网页界面"; Filename: "{app}\start_web_ui.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Tasks: desktopicon

[Run]
; 异步解压（nowait 不阻塞 UI 线程，窗口可拖拽/最小化），完成后写 .extract_done 标记
Filename: "cmd.exe"; Parameters: "/c ""{app}\7za.exe"" x ""{app}\app.7z"" -o""{app}"" -y && echo OK > ""{app}\.extract_done"""; WorkingDir: "{app}"; Flags: runhidden nowait
; 安装完成页"立即运行"复选框（默认勾选），以当前用户启动网页界面
Filename: "cmd.exe"; Parameters: "/c ""{app}\start_web_ui.bat"""; Description: "启动 VoxCPM2 TTS 网页界面"; WorkingDir: "{app}"; Flags: nowait postinstall runascurrentuser skipifsilent
; 安装完成页可选启动交互菜单（默认不勾选）
Filename: "cmd.exe"; Parameters: "/c ""{app}\Scripts\Launch_TTS_Menu.bat"""; Description: "启动 VoxCPM2 TTS 交互菜单"; WorkingDir: "{app}"; Flags: nowait postinstall runascurrentuser skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
type
  TMyMsg = record
    hwnd: HWND;
    message: UINT;
    wParam: LongWord;
    lParam: LongInt;
    time: DWORD;
    pt: TPoint;
  end;

function PeekMessage(var Msg: TMyMsg; hWnd: HWND; wMsgFilterMin, wMsgFilterMax, wRemoveMsg: UINT): BOOL; external 'PeekMessageA@user32.dll stdcall';
function TranslateMessage(const Msg: TMyMsg): BOOL; external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(const Msg: TMyMsg): LongInt; external 'DispatchMessageA@user32.dll stdcall';

{ 泵消息：让安装向导在等待期间仍能响应拖拽/最小化 }
procedure PumpMessages;
var
  Msg: TMyMsg;
begin
  while PeekMessage(Msg, 0, 0, 0, 1) do
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

{ 解压完成后清理归档与解压器（含完成标记） }
procedure RunCleanup(AppDir: string);
var
  ResultCode: Integer;
begin
  Exec('cmd.exe', '/c del /q "' + AppDir + '\app.7z" "' + AppDir + '\7za.exe" "' + AppDir + '\.extract_done"',
       AppDir, SW_HIDE, ewNoWait, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, DoneFile: string;
begin
  { 静默安装：无 UI，阻塞等待解压完成再清理（不影响交互体验） }
  if CurStep = ssPostInstall then
  begin
    if WizardSilent then
    begin
      AppDir := ExpandConstant('{app}');
      DoneFile := AppDir + '\.extract_done';
      while not FileExists(DoneFile) do
        Sleep(200);
      RunCleanup(AppDir);
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  AppDir, DoneFile: string;
begin
  { 完成页：若解压仍在后台进行，禁用“完成”按钮并边泵消息边等待（UI 仍响应） }
  if CurPageID = wpFinished then
  begin
    AppDir := ExpandConstant('{app}');
    DoneFile := AppDir + '\.extract_done';
    if not FileExists(DoneFile) then
    begin
      WizardForm.NextButton.Enabled := False;
      while not FileExists(DoneFile) do
      begin
        PumpMessages;
        Sleep(100);
      end;
      RunCleanup(AppDir);
      WizardForm.NextButton.Enabled := True;
    end
    else
    begin
      RunCleanup(AppDir);
    end;
  end;
end;
