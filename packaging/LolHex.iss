; 海克斯大乱斗助手安装包。由 packaging/package.ps1 调用（build.ps1 打完程序目录后也走它）。
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; 程序目录和输出目录可以从外面指定：正式包是在别处打好、签过名的目录（见 packaging/package.ps1）
#ifndef Dist
  #define Dist "..\build\dist\AramHex"
#endif
#ifndef OutDir
  #define OutDir "..\build\installer"
#endif

[Setup]
AppId={{1107079E-6C48-46FD-938D-04B1C3EA9A72}
AppName=AramHex 海克斯大乱斗助手
AppVersion={#AppVersion}
AppPublisher=czgreat
AppPublisherURL=https://github.com/czgreat/aramhex
AppSupportURL=https://github.com/czgreat/aramhex/issues
AppUpdatesURL=https://github.com/czgreat/aramhex/releases
DefaultDirName={localappdata}\Programs\AramHex
DefaultGroupName=海克斯大乱斗助手
; 装到用户目录，不需要管理员权限；始终"只为当前用户安装"（卸载信息写在 HKCU），在线更新才能改「应用」列表里的版本号
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
OutputDir={#OutDir}
OutputBaseFilename=AramHex-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=海克斯大乱斗助手
UninstallDisplayIcon={app}\AramHex.exe
SetupIconFile=lolhex.ico
#ifdef Terms
LicenseFile={#Terms}
#else
LicenseFile=..\LICENSE
#endif

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
#ifdef Terms
Source: "{#Terms}"; DestDir: "{app}"; DestName: "使用条款.txt"; Flags: ignoreversion
#endif

[InstallDelete]
; 升级时先清掉旧的程序文件（onedir 目录合并安装，否则旧版本多出来的文件会留下）
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; 增量更新会往 _internal 里加安装时没有的文件，卸载记录里没有它们，这里整目录删掉
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\海克斯大乱斗助手"; Filename: "{app}\AramHex.exe"
Name: "{group}\卸载海克斯大乱斗助手"; Filename: "{uninstallexe}"
Name: "{userdesktop}\海克斯大乱斗助手"; Filename: "{app}\AramHex.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AramHex.exe"; Description: "立即启动"; Flags: nowait postinstall skipifsilent
; 助手里点「立即更新」走的是 /SILENT：装完自动重新打开
Filename: "{app}\AramHex.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; 覆盖旧版卸载日志里带相同 RunOnceId 的条目。实际清理在下面按安装路径执行。
Filename: "{cmd}"; Parameters: "/d /c exit /b 0"; Flags: runhidden; RunOnceId: "KillAramHex"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure CloseInstalledAramHex(const ExePath: String);
var
  Locator, Service, Processes, Process, ProcessPath: Variant;
  I: Integer;
begin
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := Service.ExecQuery('SELECT * FROM Win32_Process WHERE Name = ''AramHex.exe''');
    for I := 0 to Processes.Count - 1 do
    begin
      Process := Processes.ItemIndex(I);
      ProcessPath := Process.ExecutablePath;
      if not VarIsNull(ProcessPath) then
      begin
        if CompareText(ProcessPath, ExePath) = 0 then
        begin
          if Process.Terminate(0) <> 0 then
            Log('Could not close AramHex at the installed path.');
        end;
      end;
    end;
  except
    Log('Could not check running AramHex processes.');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExePath, Command: String;
begin
  ExePath := ExpandConstant('{app}\AramHex.exe');
  if CurUninstallStep = usUninstall then
    CloseInstalledAramHex(ExePath);
  if CurUninstallStep = usPostUninstall then
  begin
    if RegQueryStringValue(HKCU, RunKey, 'AramHex', Command) then
    begin
      if CompareText(Command, '"' + ExePath + '" --minimized') = 0 then
      begin
        if not RegDeleteValue(HKCU, RunKey, 'AramHex') then
          Log('Could not remove the installed AramHex startup value.');
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Only a completed full installation can retire an incomplete rollback. }
    DeleteFile(ExpandConstant('{app}\update-rollback-incomplete.txt'));
    DeleteFile(ExpandConstant('{app}\update-failed.txt'));
    DelTree(ExpandConstant('{app}\_update_old'), True, True, True);
    DelTree(ExpandConstant('{app}\_update_new'), True, True, True);
  end;
end;
