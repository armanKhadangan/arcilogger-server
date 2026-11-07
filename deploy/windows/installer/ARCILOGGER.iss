; ARCILOGGER-Setup-<version>.exe (Inno Setup 6.3 or newer; "x64compatible" needs 6.3).
;
; Compiled by Build-ArciBundle.ps1:
;   ISCC.exe /DBundleDir=<dist\ARCILOGGER-x.y.z> /DAppVersion=x.y.z ARCILOGGER.iss
;
; The setup only carries the verified offline bundle and runs installer\Install-Arcilogger.ps1 -Auto
; in a visible PowerShell window (the PostgreSQL superuser password is asked there, once). All the
; work - checks, folders, rights, services, database, firewall, tasks - is done by that script, so
; installing with or without this .exe gives the same result. No third-party binary is stored in
; the repository: they come from the bundle, which the build downloads and verifies.
;
; Two command-line switches of the setup are passed on to the script (licensing contract 9.3,
; owner decision C-6; docs\KURULUM.md section 7, docs\LISANS.md section 7):
;   ARCILOGGER-Setup-x.y.z.exe /LICENSEFILE="C:\Kurulum\ARCI-L-....lic"  -> -LicenseFile "<full path>"
;   ARCILOGGER-Setup-x.y.z.exe /ALLOWUNCOVEREDUPGRADE                      -> -AllowUncoveredUpgrade
; Without them the script gets -Auto only, as before.
; The file is UTF-8 with BOM (Turkish messages).

#ifndef BundleDir
  #error BundleDir is not defined - build with Build-ArciBundle.ps1 (ISCC /DBundleDir=...)
#endif
#ifndef AppVersion
  #error AppVersion is not defined - build with Build-ArciBundle.ps1 (ISCC /DAppVersion=...)
#endif

[Setup]
AppId={{931384D1-CF20-481E-A741-D2E250F48909}
AppName=ARCILOGGER
AppVersion={#AppVersion}
AppVerName=ARCILOGGER {#AppVersion}
AppPublisher=ARCILOGGER
; Only the uninstaller lives here; the program goes to C:\Program Files\ARCILOGGER (Install-Arcilogger.ps1).
DefaultDirName={commonappdata}\ARCILOGGER\setup
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
PrivilegesRequired=admin
; x64compatible also admits Arm64 Windows 11 (x64 emulation): the script's preflight warns there.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows Server 2019 / Windows 10 1809 or newer (tar.exe). What is supported is decided by the
; script's preflight (Server 2022/2025, Windows 11 Pro/Enterprise/Education), which explains it.
MinVersion=10.0.17763
OutputBaseFilename=ARCILOGGER-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto
CloseApplications=no
RestartApplications=no
SetupLogging=yes
Uninstallable=yes
UninstallDisplayName=ARCILOGGER {#AppVersion}
; The program's icon (deploy\windows\desktop\icons, in the bundle): Setup, its uninstaller and the
; entry in Settings > Apps.
SetupIconFile={#BundleDir}\desktop\icons\arcilogger.ico
UninstallDisplayIcon={uninstallexe}

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
turkish.RunInstaller=ARCILOGGER kuruluyor. Açılan PowerShell penceresini izleyin; eksik kurulum bilgileri (kuruluş adı, saat kaynağı, yönetici PC'sinin adresi) ve PostgreSQL parolası orada sorulur, ilk kurulumda kurulum anahtarı da orada gösterilir. Pencere Enter'a basınca kapanır.
english.RunInstaller=Installing ARCILOGGER. Follow the PowerShell window; it asks for missing setup values (organisation name, time source, admin PC address) and the PostgreSQL password, and shows the setup token of a first installation. The window closes when you press Enter.
turkish.InstallerFailed=ARCILOGGER kurulumu %1 koduyla bitti. Ayrıntılar PowerShell penceresinde ve kurulum günlüğünde: %%TEMP%%\ARCILOGGER-*.log (veri klasörü oluştuysa bir kopyası onun logs klasöründe; pencerenin sonunda yolu yazılır). Hatayı giderip kurulumu yeniden çalıştırabilirsiniz.
english.InstallerFailed=The ARCILOGGER installer ended with code %1. See the PowerShell window and the install log: %%TEMP%%\ARCILOGGER-*.log (with a copy in the logs folder of the data folder once that exists; the window shows the path at the end). Fix the problem and run the setup again.
turkish.InstallerWarnings=ARCILOGGER kuruldu, ancak uyarılar var (kod 1). PowerShell penceresindeki sağlık denetimi sonuçlarını okuyun.
english.InstallerWarnings=ARCILOGGER is installed with warnings (code 1). Read the health check results in the PowerShell window.
turkish.InstallerNotRecorded=ARCILOGGER kurulumu %1 koduyla bitti, ancak bu paket kurulmadı: %2 bu paketin parmak izini göstermiyor. PowerShell betiği ilk satırından önce durmuş olabilir (sözdizimi ya da parametre hatası). Betiği yönetici konsolundan çalıştırıp hatayı okuyun (docs\KURULUM.md, bölüm 15).
english.InstallerNotRecorded=The ARCILOGGER installer ended with code %1, but this bundle was not installed: %2 does not name its fingerprint. The PowerShell script may have stopped before its first line (a syntax or parameter error). Run it from an administrator console and read the error (docs\INSTALL.md, section 15).
turkish.LicenseFileMissing=Lisans dosyası bulunamadı: "%1" (/LICENSEFILE). Dosyanın yolunu tırnak içinde verin, ör. /LICENSEFILE="C:\Kurulum\ARCI-L-....lic". Kurulum hiçbir şey değiştirmeden kapanıyor.
english.LicenseFileMissing=Licence file not found: "%1" (/LICENSEFILE). Give the path of the file in quotes, e.g. /LICENSEFILE="C:\Kurulum\ARCI-L-....lic". The setup closes without changing anything.
turkish.KeepData=Kaldırma; hizmetleri, görevleri, güvenlik duvarı kurallarını ve program dosyalarını siler. Ham loglar, kasa, yapılandırma, gizli depo ve veritabanı SİLİNMEZ.
english.KeepData=Removal deletes services, tasks, firewall rules and program files. Raw logs, the vault, the configuration, the secret store and the database are NOT deleted.
turkish.RemoveDataQuestion=ARCILOGGER kaldırılıyor: hizmetler, görevler, güvenlik duvarı kuralları ve program dosyaları silinir.%n%nHam loglar, kasa (mühürlü günler), yedekler, yapılandırma, gizli depo ve veritabanı varsayılan olarak KORUNUR.%n%nTÜM VERİLER DE SİLİNSİN Mİ? Bu GERİ ALINAMAZ.%n%nEvet: PowerShell penceresi silinecekleri gösterir, postgres parolasını ve onay için kuruluş adını ister. ARCILOGGER kayıtları 5651 yükümlülüklerini desteklemek için en az 730 gün saklar; süresi dolmamış kayıtları silmeden önce hukuk danışmanınıza danışın.%nHayır (önerilen): veriler korunur.
english.RemoveDataQuestion=Removing ARCILOGGER: services, tasks, firewall rules and program files are deleted.%n%nRaw logs, the vault (sealed days), backups, the configuration, the secret store and the database are KEPT by default.%n%nDELETE ALL THE DATA AS WELL? This CANNOT be undone.%n%nYes: the PowerShell window shows what goes and asks for the postgres password and, to confirm, the organisation name. ARCILOGGER keeps its records for at least 730 days to support the 5651 obligations; ask your legal counsel before deleting records whose retention has not run out.%nNo (recommended): the data is kept.
turkish.UninstallFailed=ARCILOGGER kaldırma betiği %1 koduyla bitti; ARCILOGGER tam kaldırılmadı (kod 2: hiçbir şey kaldırılmadı, örneğin bir mühürleme görevi hâlâ çalışıyordu ya da kuruluş adı onaylanmadı). PowerShell penceresindeki iletiyi okuyun, sorunu giderip yönetici konsolunda yeniden çalıştırın: powershell -ExecutionPolicy Bypass -File "%2" -Uninstall%3
english.UninstallFailed=The ARCILOGGER removal script ended with code %1; ARCILOGGER was not removed completely (code 2: nothing was removed, e.g. a sealing task was still running or the organisation name was not confirmed). Read the PowerShell window, fix the problem and run it again from an administrator console: powershell -ExecutionPolicy Bypass -File "%2" -Uninstall%3
turkish.DataNotRemoved=Veriler SİLİNMEDİ: kuruluş adı onaylanmadı ya da bir denetim durdurdu (PowerShell penceresindeki iletiye bakın). ARCILOGGER şimdi verileri KORUNARAK kaldırılıyor. Verileri sonra silmek için: docs\KURULUM.md bölüm 14.1.
english.DataNotRemoved=The data was NOT deleted: the organisation name was not confirmed or a check stopped it (see the PowerShell window). ARCILOGGER is now removed with its data KEPT. To delete the data later: docs\INSTALL.md section 14.1.

[Files]
Source: "{#BundleDir}\*"; DestDir: "{tmp}\bundle"; Flags: recursesubdirs createallsubdirs ignoreversion deleteafterinstall

[Code]
var
  { -LicenseFile and -AllowUncoveredUpgrade for the script, from the setup's command line
    (InitializeSetup). Empty / False: the script gets -Auto only. }
  LicenseFileArgument: String;
  AllowUncoveredUpgrade: Boolean;

function PowerShellPath(): String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function VersionFile(): String;
begin
  Result := ExpandConstant('{commonpf64}\ARCILOGGER\VERSION.json');
end;

{ The bundle fingerprint as Install-Arcilogger.ps1 records it in VERSION.json: the SHA-256 of the
  bundle's SHA256SUMS, lower-case hex (GetSHA256OfFile: Inno Setup 6.3 or newer). Empty when the
  file cannot be read; the installer then stops at its bundle check anyway. }
function BundleFingerprint(): String;
begin
  try
    Result := Lowercase(GetSHA256OfFile(ExpandConstant('{tmp}\bundle\SHA256SUMS')));
  except
    Result := '';
  end;
end;

{ True when VERSION.json names this fingerprint, i.e. the installer wrote it for this bundle (a
  new installation, an upgrade or a repair of the same bundle). PowerShell also ends with 1 when
  a script stops before its first line runs (a parse or parameter error): then VERSION.json is
  missing or still names the previous bundle. }
function BundleIsInstalled(const Fingerprint: String): Boolean;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if (Fingerprint = '') or not LoadStringsFromFile(VersionFile(), Lines) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
    if (Pos('"fingerprint"', Lines[I]) > 0) and (Pos(Fingerprint, Lowercase(Lines[I])) > 0) then
    begin
      Result := True;
      Exit;
    end;
end;

{ True when Param starts like a switch: / or - (Copy, not Param[1]: safe on an empty string). }
function HasSwitchPrefix(const Param: String): Boolean;
begin
  Result := (Copy(Param, 1, 1) = '/') or (Copy(Param, 1, 1) = '-');
end;

{ True when Param is the switch /Name or -Name (any case), without a value. }
function IsSwitch(const Param, Name: String): Boolean;
begin
  Result := HasSwitchPrefix(Param) and (Lowercase(Copy(Param, 2, Length(Param))) = Lowercase(Name));
end;

{ True when the setup's command line holds the switch /Name or -Name (any case). }
function SwitchGiven(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if IsSwitch(ParamStr(I), Name) then
      Result := True;
end;

{ The value of /Name=value or -Name=value (any case; the last one wins). ParamStr has already
  removed the quotes of /Name="C:\a b\c.lic". Found tells whether the switch was given at all,
  also as /Name without "=" (then the value is empty). }
function SwitchValue(const Name: String; var Found: Boolean): String;
var
  I: Integer;
  Param, Head: String;
begin
  Result := '';
  Found := False;
  Head := Lowercase(Name) + '=';
  for I := 1 to ParamCount do
  begin
    Param := ParamStr(I);
    if IsSwitch(Param, Name) then
    begin
      Found := True;
      Result := '';
    end
    else if HasSwitchPrefix(Param) and (Lowercase(Copy(Param, 2, Length(Head))) = Head) then
    begin
      Found := True;
      Result := Trim(Copy(Param, Length(Head) + 2, Length(Param)));
    end;
  end;
end;

{ The full path of the licence file: as given, or relative to the current folder, else to the
  folder of this .exe (the src constant). The script runs in the temporary bundle folder, so it only
  ever gets a full path. Empty when there is no such file. A Windows path cannot contain a double
  quote, so the quoted argument (InstallerParameters) is always one argument; a value with one is
  refused here. }
function ResolveLicenseFile(const Value: String): String;
var
  Candidate: String;
begin
  Result := '';
  if (Value = '') or (Pos('"', Value) > 0) then
    Exit;
  Candidate := ExpandFileName(Value);
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    Exit;
  end;
  if (ExtractFileDrive(Value) = '') and (Copy(Value, 1, 1) <> '\') then
  begin
    Candidate := ExpandFileName(AddBackslash(ExpandConstant('{src}')) + Value);
    if FileExists(Candidate) then
      Result := Candidate;
  end;
end;

{ Before the wizard: read /LICENSEFILE and /ALLOWUNCOVEREDUPGRADE. A /LICENSEFILE that names no
  file stops the setup before anything is copied (the script would stop at its preflight anyway,
  exit code 2). Without the switches nothing changes. }
function InitializeSetup(): Boolean;
var
  Given: String;
  Found: Boolean;
begin
  Result := True;
  AllowUncoveredUpgrade := SwitchGiven('ALLOWUNCOVEREDUPGRADE');
  LicenseFileArgument := '';
  Given := SwitchValue('LICENSEFILE', Found);
  if Found then
  begin
    LicenseFileArgument := ResolveLicenseFile(Given);
    if LicenseFileArgument = '' then
    begin
      SuppressibleMsgBox(FmtMessage(CustomMessage('LicenseFileMissing'), [Given]), mbCriticalError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

{ The script's command line: -Auto as always, then the switches given to the setup. }
function InstallerParameters(): String;
begin
  Result := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\bundle\installer\Install-Arcilogger.ps1') + '" -Auto';
  if LicenseFileArgument <> '' then
    Result := Result + ' -LicenseFile "' + LicenseFileArgument + '"';
  if AllowUncoveredUpgrade then
    Result := Result + ' -AllowUncoveredUpgrade';
end;

procedure RunArciInstaller();
var
  ResultCode: Integer;
  Parameters: String;
  Fingerprint: String;
begin
  WizardForm.StatusLabel.Caption := CustomMessage('RunInstaller');
  Fingerprint := BundleFingerprint();
  Parameters := InstallerParameters();
  if not Exec(PowerShellPath(), Parameters, ExpandConstant('{tmp}\bundle'), SW_SHOW, ewWaitUntilTerminated, ResultCode) then
    MsgBox(FmtMessage(CustomMessage('InstallerFailed'), [SysErrorMessage(ResultCode)]), mbCriticalError, MB_OK)
  else if (ResultCode <> 0) and (ResultCode <> 1) then
    MsgBox(FmtMessage(CustomMessage('InstallerFailed'), [IntToStr(ResultCode)]), mbCriticalError, MB_OK)
  else if not BundleIsInstalled(Fingerprint) then
    MsgBox(FmtMessage(CustomMessage('InstallerNotRecorded'), [IntToStr(ResultCode), VersionFile()]), mbCriticalError, MB_OK)
  else if ResultCode = 1 then
    MsgBox(CustomMessage('InstallerWarnings'), mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunArciInstaller();
end;

{ Install-Arcilogger.ps1 -Uninstall (with -RemoveData when Extra says so) in a visible window. }
function RunRemoval(const Script, Extra: String; var ResultCode: Integer): Boolean;
begin
  Result := Exec(PowerShellPath(), '-NoProfile -ExecutionPolicy Bypass -File "' + Script + '" -Uninstall' + Extra, '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  Script: String;
  RemoveData: String;
  Ran: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    Script := ExpandConstant('{commonpf64}\ARCILOGGER\installer\Install-Arcilogger.ps1');
    if FileExists(Script) then
    begin
      // "Tüm verileri de sil" (contract addendum C8, owner decision Q6): No is the default
      // button, and a silent uninstall (/SILENT, /VERYSILENT) asks nothing and keeps the data.
      // The script itself shows what goes and asks for the organisation name before anything
      // is changed.
      RemoveData := '';
      if not UninstallSilent() then
      begin
        if SuppressibleMsgBox(CustomMessage('RemoveDataQuestion'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
          RemoveData := ' -RemoveData'
        else
          SuppressibleMsgBox(CustomMessage('KeepData'), mbInformation, MB_OK, IDOK);
      end;
      // The script keeps its own folder (installer\) until the very end, so it can be run again
      // when it stops (a task still running, a file Windows refuses to delete).
      Ran := RunRemoval(Script, RemoveData, ResultCode);
      // 2 with -RemoveData: nothing was removed (the name was not confirmed, a check stopped
      // it). The uninstaller goes on and drops its entry whatever happens, so the program is
      // removed now with the data kept, rather than left running without an entry.
      if Ran and (RemoveData <> '') and (ResultCode = 2) then
      begin
        SuppressibleMsgBox(CustomMessage('DataNotRemoved'), mbInformation, MB_OK, IDOK);
        RemoveData := '';
        Ran := RunRemoval(Script, RemoveData, ResultCode);
      end;
      if not Ran then
        MsgBox(FmtMessage(CustomMessage('UninstallFailed'), [SysErrorMessage(ResultCode), Script, RemoveData]), mbCriticalError, MB_OK)
      else if (ResultCode <> 0) and (ResultCode <> 1) then
        MsgBox(FmtMessage(CustomMessage('UninstallFailed'), [IntToStr(ResultCode), Script, RemoveData]), mbCriticalError, MB_OK);
    end;
  end;
end;
