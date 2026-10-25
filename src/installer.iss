; 帝国开局助手安装器。参数契约见 release_build.py 及 RELEASE.md。
; PayloadDir（待打包目录）、AppVersion（产品版本）由构建脚本传入。
#ifndef PayloadDir
  #error PayloadDir must be supplied by release_build.py
#endif
#ifndef AppVersion
  #define AppVersion "1.6.0"
#endif
#ifdef TestBuild
  ; 隔离安装测试使用独立标识和快捷方式，不污染真正发行安装。
  #define ProductAppId "{{A40CDF9F-AF3D-4D26-A68B-76EC135FE60A}"
  #define ProductName "AoE4 Installer QA"
  #define ProductMutex "AoE4OpeningAssistant.Setup.QA"
#else
  #define ProductAppId "{{8B5744A8-F2C0-48E4-85D9-55BB537FBB11}"
  #define ProductName "帝国开局助手"
  #define ProductMutex "AoE4OpeningAssistant.Setup"
#endif
#ifndef WaitTimeoutMs
  #define WaitTimeoutMs 60000
#endif

[Setup]
AppId={#ProductAppId}
AppName={#ProductName}
AppVersion={#AppVersion}
AppPublisher=帝国开局助手开源项目
DefaultDirName={localappdata}\Programs\AoE4OpeningAssistant
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=AoE4OpeningAssistant-Setup
SetupIconFile={#PayloadDir}\源码\界面素材\龙序.ico
UninstallDisplayIcon={app}\启动中文助手.exe
LicenseFile={#PayloadDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
CloseApplications=no
RestartApplications=no
SetupMutex={#ProductMutex}
UninstallLogMode=append
; 不创建任何用户数据清理规则。卸载只移除本安装器部署的程序文件。

[Languages]
Name: "chinesesimplified"; MessagesFile: "installer_resources\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
; 仅从无用户数据的构建暂存目录取文件，双重排除个人目录。
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "用户数据,用户数据\*,离线语音包,离线语音包\*,__pycache__,*.pyc,战绩缓存,战绩缓存\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#ProductName}"; Filename: "{app}\启动中文助手.exe"; Parameters: "{code:ApplicationArguments}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#ProductName}"; Filename: "{app}\启动中文助手.exe"; Parameters: "{code:ApplicationArguments}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 普通安装保留复选框；软件内更新通过 /RESTART_APP=1 在完成阶段启动一次。
Filename: "{app}\启动中文助手.exe"; Parameters: "{code:ApplicationArguments}"; WorkingDir: "{app}"; Description: "启动帝国开局助手"; Flags: nowait postinstall skipifsilent; Check: IsInteractiveLaunch

[Code]
const
  SynchronizeAccess = $00100000;
var
  DataPage: TInputDirWizardPage;
  ChosenDataDir: String;
  ExplicitDataDir: Boolean;

function OpenProcess(DesiredAccess: LongWord; InheritHandle: Boolean; ProcessId: LongWord): LongWord;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: LongWord; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsAbsoluteDirectory(const Value: String): Boolean;
begin
  Result := ((Length(Value) >= 3) and (Value[2] = ':') and (Value[3] = '\')) or
    ((Length(Value) >= 3) and (Copy(Value, 1, 2) = '\\'));
end;

function JsonEscape(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  Result := Value;
end;

function ReadDataLocation(const Directory: String): String;
var
  Lines: TArrayOfString;
  Text, Digit: String;
  I, Start, CodePoint: Integer;
  Escaped: Boolean;
begin
  Result := '';
  if not LoadStringsFromFile(AddBackslash(Directory) + 'data-location.json', Lines) then Exit;
  Text := '';
  for I := 0 to GetArrayLength(Lines) - 1 do Text := Text + Lines[I];
  Start := Pos('"data_dir"', Text);
  if Start = 0 then Exit;
  I := Start + Length('"data_dir"');
  while (I <= Length(Text)) and (Text[I] <> ':') do I := I + 1;
  I := I + 1;
  while (I <= Length(Text)) and (Text[I] <= ' ') do I := I + 1;
  if (I > Length(Text)) or (Text[I] <> '"') then Exit;
  I := I + 1;
  Escaped := False;
  while I <= Length(Text) do begin
    if Escaped then begin
      if Text[I] = 'u' then begin
        if I + 4 > Length(Text) then begin Result := ''; Exit; end;
        Digit := Copy(Text, I + 1, 4);
        CodePoint := StrToIntDef('$' + Digit, -1);
        if CodePoint < 0 then begin Result := ''; Exit; end;
        Result := Result + Chr(CodePoint);
        I := I + 4;
      end else if (Text[I] = '\') or (Text[I] = '"') or (Text[I] = '/') then
        Result := Result + Text[I]
      else begin Result := ''; Exit; end;
      Escaped := False;
    end else if Text[I] = '\' then Escaped := True
    else if Text[I] = '"' then begin
      if not IsAbsoluteDirectory(Result) then Result := '';
      Exit;
    end else Result := Result + Text[I];
    I := I + 1;
  end;
  Result := '';
end;

function DefaultDataDirectory: String;
begin
  Result := ExpandConstant('{param:DATA_DIR|}');
  if Result <> '' then Exit;
  Result := ReadDataLocation(WizardDirValue);
  if Result <> '' then Exit;
  { 原有便携目录安装时自动沿用旁边的数据，绝不移动或复制旧数据。 }
  Result := AddBackslash(WizardDirValue) + '用户数据';
  if DirExists(Result) then Exit;
  Result := ExpandConstant('{userappdata}\AoE4OpeningAssistant');
end;

function InitializeSetup: Boolean;
var
  ParentId: Integer;
  ProcessHandle, WaitResult: LongWord;
begin
  Result := False;
  ParentId := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if ParentId > 0 then begin
    ProcessHandle := OpenProcess(SynchronizeAccess, False, ParentId);
    if ProcessHandle <> 0 then begin
      try
        { 等待父程序正常退出；不使用任务终止或重启管理器关闭其他实例。 }
        WaitResult := WaitForSingleObject(ProcessHandle, {#WaitTimeoutMs});
        if WaitResult <> 0 then begin
          SuppressibleMsgBox('程序尚未退出，请先保存并关闭帝国开局助手后重试。', mbError, MB_OK, IDOK);
          Exit;
        end;
      finally
        CloseHandle(ProcessHandle);
      end;
    end;
  end;
  Result := True;
end;

procedure InitializeWizard;
begin
  ExplicitDataDir := ExpandConstant('{param:DATA_DIR|}') <> '';
  DataPage := CreateInputDirPage(wpSelectDir, '选择开局和设置目录',
    '继续使用已有资料，或者为新安装选择一个目录。',
    '已有便携版时，可以选择其“用户数据”文件夹。安装和卸载都不会清除这里的开局与设置。', False, '');
  DataPage.Add('开局和设置目录：');
  ChosenDataDir := DefaultDataDirectory;
  DataPage.Values[0] := ChosenDataDir;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = DataPage.ID) and ExplicitDataDir;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    ChosenDataDir := DefaultDataDirectory;
    DataPage.Values[0] := ChosenDataDir;
  end;
  if CurPageID = DataPage.ID then begin
    ChosenDataDir := DataPage.Values[0];
    if not IsAbsoluteDirectory(ChosenDataDir) then begin
      MsgBox('请选择完整的文件夹路径。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function ApplicationArguments(Param: String): String;
begin
  Result := '--data-dir ' + AddQuotes(ChosenDataDir);
end;

function IsInteractiveLaunch: Boolean;
begin
  Result := ExpandConstant('{param:RESTART_APP|0}') <> '1';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Json: TArrayOfString;
  ExitCode: Integer;
begin
  if CurStep = ssInstall then begin
    { 静默安装也解析参数，避免新旧程序使用两个不同的数据目录。 }
    if WizardSilent then ChosenDataDir := DefaultDataDirectory
    else ChosenDataDir := DataPage.Values[0];
    if not IsAbsoluteDirectory(ChosenDataDir) then
      RaiseException('开局和设置目录必须是完整路径。');
  end;
  if CurStep = ssPostInstall then begin
    SetArrayLength(Json, 1);
    Json[0] := '{"data_dir":"' + JsonEscape(ChosenDataDir) + '"}';
    if not SaveStringsToUTF8FileWithoutBOM(ExpandConstant('{app}\data-location.json'), Json, False) then
      RaiseException('无法保存资料目录，请检查安装文件夹的写入权限。');
  end;
  if (CurStep = ssDone) and (ExpandConstant('{param:RESTART_APP|0}') = '1') then begin
    if not Exec(ExpandConstant('{app}\启动中文助手.exe'), ApplicationArguments(''),
      ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait, ExitCode) then
      SuppressibleMsgBox('更新已经安装，程序未能自动启动，请从快捷方式打开。', mbInformation, MB_OK, IDOK);
  end;
end;
