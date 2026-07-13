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
; 安装完成页"立即运行"复选框（默认勾选），以当前用户启动网页界面
Filename: "cmd.exe"; Parameters: "/c ""{app}\start_web_ui.bat"""; Description: "启动 VoxCPM2 TTS 网页界面"; WorkingDir: "{app}"; Flags: nowait postinstall runascurrentuser skipifsilent
; 安装完成页可选启动交互菜单（默认不勾选）
Filename: "cmd.exe"; Parameters: "/c ""{app}\Scripts\Launch_TTS_Menu.bat"""; Description: "启动 VoxCPM2 TTS 交互菜单"; WorkingDir: "{app}"; Flags: nowait postinstall runascurrentuser skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  CancelRequested: Boolean;
  ExtractBar: TNewProgressBar;
  ExtractLabel: TLabel;

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
procedure ExitProcess(uExitCode: UINT); external 'ExitProcess@kernel32.dll stdcall';

{ 泵消息：让安装向导在等待期间仍能响应拖拽/最小化/取消 }
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

{ 从进度日志读取最后一个百分比数字（-1 表示尚未出现） }
function ReadLastPercent(const FilePath: string): Integer;
var
  Lines: TArrayOfString;
  i, cnt, start, p: Integer;
  s, numStr: string;
begin
  Result := -1;
  if not LoadStringsFromFile(FilePath, Lines) then Exit;
  cnt := GetArrayLength(Lines);
  start := cnt - 200;
  if start < 0 then start := 0;
  for i := start to cnt - 1 do
  begin
    s := Lines[i];
    p := Pos('%', s);
    if p > 0 then
    begin
      numStr := '';
      while (p > 1) and (s[p-1] >= '0') and (s[p-1] <= '9') do
      begin
        numStr := s[p-1] + numStr;
        p := p - 1;
      end;
      if numStr <> '' then
        Result := StrToIntDef(numStr, Result);
    end;
  end;
end;

{ 创建并显示真实的解压进度控件（嵌在安装向导内置进度条下方） }
procedure ShowExtractProgress;
var
  gauge: TNewProgressBar;
  labelTop, barTop: Integer;
begin
  gauge := WizardForm.ProgressGauge;
  labelTop := gauge.Top + gauge.Height + ScaleY(24);
  barTop := labelTop + ScaleY(20);

  ExtractLabel := TLabel.Create(WizardForm);
  ExtractLabel.Parent := WizardForm;
  ExtractLabel.Left := gauge.Left;
  ExtractLabel.Top := labelTop;
  ExtractLabel.Width := gauge.Width;
  ExtractLabel.Caption := '正在解压资源文件，请稍候...';
  ExtractLabel.Visible := True;

  ExtractBar := TNewProgressBar.Create(WizardForm);
  ExtractBar.Parent := WizardForm;
  ExtractBar.Left := gauge.Left;
  ExtractBar.Top := barTop;
  ExtractBar.Width := gauge.Width;
  ExtractBar.Height := gauge.Height;
  ExtractBar.Min := 0;
  ExtractBar.Max := 100;
  ExtractBar.Position := 0;
  ExtractBar.Visible := True;
end;

procedure HideExtractProgress;
begin
  if ExtractLabel <> nil then
  begin
    ExtractLabel.Free;
    ExtractLabel := nil;
  end;
  if ExtractBar <> nil then
  begin
    ExtractBar.Free;
    ExtractBar := nil;
  end;
end;

{ 解压完成后清理归档与解压器（含完成/取消标记） }
procedure RunCleanup(AppDir: string);
var
  ResultCode: Integer;
begin
  Exec('cmd.exe', '/c del /q "' + AppDir + '\app.7z" "' + AppDir + '\7za.exe" "' + AppDir + '\.extract_done" "' + AppDir + '\.extract_cancelled"',
       AppDir, SW_HIDE, ewNoWait, ResultCode);
end;

{ 等待后台 7z 解压完成，其间更新真实解压进度 }
procedure WaitForExtract(AppDir: string);
var
  DoneFile, CancelFile, LogFile: string;
  ResultCode, pct: Integer;
begin
  DoneFile := AppDir + '\.extract_done';
  CancelFile := AppDir + '\.extract_cancelled';
  LogFile := ExpandConstant('{tmp}') + '\extract_progress.log';

  ShowExtractProgress;

  { ssPostInstall 期间默认禁用取消按钮，这里强制启用 }
  WizardForm.CancelButton.Enabled := True;

  { 启动 7za 异步解压：进度(-bsp1)与日志分离(-bso0)，进度写入日志；完成后写 .extract_done }
  Exec('cmd.exe', '/c ""' + AppDir + '\7za.exe"" x ""' + AppDir + '\app.7z"" -o""' + AppDir + '"" -y -bsp1 -bso0 > ""' + LogFile + '"" 2>&1 && echo OK > ""' + DoneFile + '""',
       AppDir, SW_HIDE, ewNoWait, ResultCode);

  while (not FileExists(DoneFile)) and (not FileExists(CancelFile)) do
  begin
    if CancelRequested then Break;
    pct := ReadLastPercent(LogFile);
    if pct >= 0 then
    begin
      ExtractBar.Position := pct;
      ExtractLabel.Caption := '正在解压资源文件，请稍候... ' + IntToStr(pct) + '%';
    end;
    PumpMessages;
    Sleep(150);
  end;

  { 收尾：置满进度并短暂停留，让“100%”可见 }
  ExtractBar.Position := 100;
  ExtractLabel.Caption := '解压完成，正在收尾...';
  PumpMessages;
  Sleep(400);

  HideExtractProgress;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, CancelFile: string;
  ResultCode: Integer;
begin
  { 安装后阶段：真实解压进度，完成后清理，避免完成页“假完成” }
  if CurStep = ssPostInstall then
  begin
    AppDir := ExpandConstant('{app}');
    WaitForExtract(AppDir);
    CancelFile := AppDir + '\.extract_cancelled';
    if FileExists(CancelFile) then
    begin
      RunCleanup(AppDir);
      MsgBox('安装已取消。', mbInformation, MB_OK);
      ExitProcess(1);
    end
    else
    begin
      RunCleanup(AppDir);
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  AppDir: string;
begin
  { 完成页：解压已在 ssPostInstall 中完成，只做保险清理 }
  if CurPageID = wpFinished then
  begin
    AppDir := ExpandConstant('{app}');
    if FileExists(AppDir + '\.extract_done') then
      RunCleanup(AppDir);
  end;
end;

{ 取消按钮：终止后台 7z，写取消标记，让安装退出 }
procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
var
  AppDir: string;
  ResultCode: Integer;
begin
  Cancel := True;
  Confirm := False; { 不显示确认对话框，点击取消直接退出 }
  AppDir := ExpandConstant('{app}');
  Exec('taskkill.exe', '/f /im 7za.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('cmd.exe', '/c echo CANCELLED > "' + AppDir + '\.extract_cancelled"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  CancelRequested := True;
end;
