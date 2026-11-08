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
turkish.SelfTestLine=Denetim: %1 tamam, %2 uyarı/eksik — ayrıntılar PowerShell penceresinde ve günlükte.
english.SelfTestLine=Self-test: %1 ok, %2 warnings/missing — details in the PowerShell window and the log.
turkish.OrganisationTitle=Kurum bilgileri
english.OrganisationTitle=Organisation
turkish.OrganisationDescription=ARCILOGGER'ın raporlarda, e-postalarda ve güvenlik duvarında kullandığı bilgiler.
english.OrganisationDescription=What ARCILOGGER uses in reports, e-mails and the firewall.
turkish.OrganisationHint=Bu sayfa yalnızca ilk kurulumda sorulur; değerler sonra web arayüzünde değiştirilebilir.
english.OrganisationHint=This page is asked on a first installation only; the values can be changed later in the web interface.
turkish.SiteNamePrompt=Kuruluş adı (zorunlu):
english.SiteNamePrompt=Organisation name (required):
turkish.NtpServerPrompt=Saat sunucusu (isteğe bağlı; genellikle ana yönlendirici, ad ya da IPv4 adresi):
english.NtpServerPrompt=Time server (optional; usually the core router, a name or an IPv4 address):
turkish.AdminAddressPrompt=Yönetici bilgisayarının adresi (zorunlu; IPv4 ya da IPv4/önek, virgülle ayrılmış):
english.AdminAddressPrompt=Admin PC address (required; IPv4 or IPv4/prefix, comma separated):
turkish.ProviderTitle=Zaman damgası
english.ProviderTitle=Time stamping
turkish.ProviderDescription=Günlük logları mühürleyen zaman damgası sağlayıcısı.
english.ProviderDescription=The time-stamp provider that seals the daily logs.
turkish.ProviderHint=Kullanıcı adı ve parola (ya da Kamu SM müşteri bilgileri) kurulumdan sonra web arayüzünde, Ayarlar → Zaman damgası'nda girilir. Sağlayıcı sonra da değiştirilebilir.
english.ProviderHint=The user name and password (or the Kamu SM customer details) are entered after the installation in the web interface, under Settings → Time stamping. The provider can be changed later too.
turkish.ProviderTurktrust=TÜRKTRUST (önerilen)
english.ProviderTurktrust=TÜRKTRUST (recommended)
turkish.ProviderZamane=Kamu SM (Zamane; Java ve Zamane istemcisi gerekir)
english.ProviderZamane=Kamu SM (Zamane; needs Java and the Zamane client)
turkish.FieldInvalid=%1 %2
english.FieldInvalid=%1 %2
turkish.FieldRequired=boş olamaz
english.FieldRequired=must not be empty
turkish.FieldTooLong=en çok %1 karakter
english.FieldTooLong=at most %1 characters
turkish.FieldCharacters=tırnak ("), denetim karakteri, başta "-" ya da sonda "\" olamaz
english.FieldCharacters=no quote ("), control character, leading "-" or trailing "\"
turkish.FieldHost=ad ya da IPv4 adresi, boşluksuz
english.FieldHost=a name or an IPv4 address, without spaces
turkish.FieldAddresses=geçersiz adres: %1 (ör. 172.20.10.15 ya da 172.20.10.0/24)
english.FieldAddresses=invalid address: %1 (e.g. 172.20.10.15 or 172.20.10.0/24)
turkish.SetupValuesRefused=ARCILOGGER kurulumu 2 koduyla durdu; hiçbir şey değiştirilmedi. Kurulum sayfalarındaki bir değer (kuruluş adı, saat sunucusu ya da yönetici adresi) ya da başka bir ön denetim kabul edilmedi: PowerShell penceresindeki [HATA] satırı hangisi olduğunu yazar. Setup'ı yeniden çalıştırıp düzeltin.
english.SetupValuesRefused=The ARCILOGGER installer stopped with code 2; nothing was changed. A value of the setup pages (organisation name, time server or admin address) or another preflight check was refused: the [HATA] line in the PowerShell window names it. Run the setup again and correct it.
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
  { The setup pages of a first installation (0.4.0 D2): organisation and time stamping. }
  OrganisationPage: TInputQueryWizardPage;
  ProviderPage: TInputOptionWizardPage;
  { The script ran to the end with 0 or 1 and recorded this bundle: its self-test is shown. }
  InstallerSucceeded: Boolean;

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

{ No ARCILOGGER is installed and no configuration is kept: the setup pages are shown (0.4.0
  D2). An upgrade, a repair or an installation over a configuration that a removal kept (the
  data stayed) keeps the configured values and never shows them. }
function FirstInstallation(): Boolean;
begin
  Result := not FileExists(VersionFile()) and
    not FileExists(ExpandConstant('{commonappdata}\ARCILOGGER\arcilogger.toml'));
end;

procedure InitializeWizard();
begin
  OrganisationPage := CreateInputQueryPage(wpWelcome, CustomMessage('OrganisationTitle'),
    CustomMessage('OrganisationDescription'), CustomMessage('OrganisationHint'));
  OrganisationPage.Add(CustomMessage('SiteNamePrompt'), False);
  OrganisationPage.Add(CustomMessage('NtpServerPrompt'), False);
  OrganisationPage.Add(CustomMessage('AdminAddressPrompt'), False);
  ProviderPage := CreateInputOptionPage(OrganisationPage.ID, CustomMessage('ProviderTitle'),
    CustomMessage('ProviderDescription'), CustomMessage('ProviderHint'), True, False);
  ProviderPage.Add(CustomMessage('ProviderTurktrust'));
  ProviderPage.Add(CustomMessage('ProviderZamane'));
  ProviderPage.SelectedValueIndex := 0;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := ((PageID = OrganisationPage.ID) or (PageID = ProviderPage.ID)) and not FirstInstallation();
end;

{ A value the script gets as one quoted argument (no quote, no control character, no leading
  "-" that would read as a parameter, no trailing backslash that would escape the quote). }
function SafeArgument(const Value: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to Length(Value) do
    if (Value[I] = '"') or (Ord(Value[I]) < 32) or (Ord(Value[I]) = 127) then
      Exit;
  if Length(Value) > 0 then
    if (Value[1] = '-') or (Value[Length(Value)] = '\') then
      Exit;
  Result := True;
end;

{ Shape only: four decimal octets 0-255, then an optional /0-32. The script checks again. }
function IsIPv4Spec(const Value: String): Boolean;
var
  I, Octets, Digits, Number, Slash: Integer;
  Part: String;
  Ch: Char;
begin
  Result := False;
  Part := Value;
  Slash := Pos('/', Part);
  if Slash > 0 then
  begin
    Number := StrToIntDef(Copy(Part, Slash + 1, Length(Part)), -1);
    if (Slash = Length(Part)) or (Number < 0) or (Number > 32) then
      Exit;
    Part := Copy(Part, 1, Slash - 1);
  end;
  Octets := 0;
  Digits := 0;
  Number := 0;
  for I := 1 to Length(Part) + 1 do
  begin
    if I > Length(Part) then
      Ch := '.'
    else
      Ch := Part[I];
    if Ch = '.' then
    begin
      if (Digits = 0) or (Number > 255) then
        Exit;
      Octets := Octets + 1;
      Digits := 0;
      Number := 0;
    end
    else if (Ch >= '0') and (Ch <= '9') and (Digits < 3) then
    begin
      Number := Number * 10 + (Ord(Ch) - Ord('0'));
      Digits := Digits + 1;
    end
    else
      Exit;
  end;
  Result := Octets = 4;
end;

{ The admin addresses, split at commas, semicolons and spaces; the first invalid one in Bad. }
function AdminAddresses(const Value: String; var Bad: String): String;
var
  I: Integer;
  Item: String;
  Ch: Char;
begin
  Result := '';
  Bad := '';
  Item := '';
  for I := 1 to Length(Value) + 1 do
  begin
    if I > Length(Value) then
      Ch := ','
    else
      Ch := Value[I];
    if (Ch = ',') or (Ch = ';') or (Ch = ' ') or (Ch = #9) then
    begin
      if Item <> '' then
      begin
        if not IsIPv4Spec(Item) then
        begin
          Bad := Item;
          Exit;
        end;
        if Result <> '' then
          Result := Result + ',';
        Result := Result + Item;
      end;
      Item := '';
    end
    else
      Item := Item + Ch;
  end;
end;

{ A host name or an IPv4 address without spaces (letters, digits, "-" and "."). }
function IsHostShape(const Value: String): Boolean;
var
  I: Integer;
begin
  Result := (Length(Value) > 0) and (Length(Value) <= 253);
  for I := 1 to Length(Value) do
    if not (((Value[I] >= 'a') and (Value[I] <= 'z')) or ((Value[I] >= 'A') and (Value[I] <= 'Z')) or
            ((Value[I] >= '0') and (Value[I] <= '9')) or (Value[I] = '-') or (Value[I] = '.')) then
      Result := False;
end;

function FieldProblem(const Prompt, Problem: String): Boolean;
begin
  MsgBox(FmtMessage(CustomMessage('FieldInvalid'), [Prompt, Problem]), mbError, MB_OK);
  Result := False;
end;

{ The organisation page checks shapes only (empty, length, characters); the script's preflight
  keeps the full checks and stops with code 2 before any change. }
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Name, Ntp, Bad: String;
begin
  Result := True;
  if (CurPageID <> OrganisationPage.ID) or WizardSilent() then
    Exit;
  Name := Trim(OrganisationPage.Values[0]);
  Ntp := Trim(OrganisationPage.Values[1]);
  if Name = '' then
    Result := FieldProblem(CustomMessage('SiteNamePrompt'), CustomMessage('FieldRequired'))
  else if Length(Name) > 200 then
    Result := FieldProblem(CustomMessage('SiteNamePrompt'), FmtMessage(CustomMessage('FieldTooLong'), ['200']))
  else if not SafeArgument(Name) then
    Result := FieldProblem(CustomMessage('SiteNamePrompt'), CustomMessage('FieldCharacters'))
  else if (Ntp <> '') and not IsHostShape(Ntp) then
    Result := FieldProblem(CustomMessage('NtpServerPrompt'), CustomMessage('FieldHost'))
  else if Trim(OrganisationPage.Values[2]) = '' then
    Result := FieldProblem(CustomMessage('AdminAddressPrompt'), CustomMessage('FieldRequired'))
  else
  begin
    AdminAddresses(OrganisationPage.Values[2], Bad);
    if Bad <> '' then
      Result := FieldProblem(CustomMessage('AdminAddressPrompt'), FmtMessage(CustomMessage('FieldAddresses'), [Bad]));
  end;
end;

{ The script's command line: -Auto as always, then the switches given to the setup, then the
  values of the setup pages of a first installation, each quoted as one argument. They are no
  secrets. A silent first installation without them lets the script ask at its console. }
function InstallerParameters(): String;
var
  Name, Ntp, Admin, Bad: String;
begin
  Result := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\bundle\installer\Install-Arcilogger.ps1') + '" -Auto';
  if LicenseFileArgument <> '' then
    Result := Result + ' -LicenseFile "' + LicenseFileArgument + '"';
  if AllowUncoveredUpgrade then
    Result := Result + ' -AllowUncoveredUpgrade';
  if not FirstInstallation() then
    Exit;
  Name := Trim(OrganisationPage.Values[0]);
  Ntp := Trim(OrganisationPage.Values[1]);
  Admin := AdminAddresses(OrganisationPage.Values[2], Bad);
  if (Name <> '') and SafeArgument(Name) then
    Result := Result + ' -SiteName "' + Name + '"';
  if Ntp = '' then
    Result := Result + ' -NoNtpServer'
  else if IsHostShape(Ntp) then
    Result := Result + ' -NtpServer "' + Ntp + '"';
  if (Admin <> '') and (Bad = '') then
    Result := Result + ' -AdminAddress "' + Admin + '"';
  if ProviderPage.SelectedValueIndex = 1 then
    Result := Result + ' -TimeStampProvider "zamane"'
  else
    Result := Result + ' -TimeStampProvider "turktrust"';
end;

function SelfTestFile(): String;
begin
  Result := ExpandConstant('{commonappdata}\ARCILOGGER\install\selftest.json');
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
  { The finish page shows the self-test of this run only: an earlier run's file goes first. }
  InstallerSucceeded := False;
  DeleteFile(SelfTestFile());
  if not Exec(PowerShellPath(), Parameters, ExpandConstant('{tmp}\bundle'), SW_SHOW, ewWaitUntilTerminated, ResultCode) then
    MsgBox(FmtMessage(CustomMessage('InstallerFailed'), [SysErrorMessage(ResultCode)]), mbCriticalError, MB_OK)
  else if (ResultCode <> 0) and (ResultCode <> 1) then
  begin
    { Code 2 of a first installation: most likely a value of the setup pages was refused. }
    if (ResultCode = 2) and FirstInstallation() then
      MsgBox(CustomMessage('SetupValuesRefused'), mbCriticalError, MB_OK)
    else
      MsgBox(FmtMessage(CustomMessage('InstallerFailed'), [IntToStr(ResultCode)]), mbCriticalError, MB_OK);
  end
  else if not BundleIsInstalled(Fingerprint) then
    MsgBox(FmtMessage(CustomMessage('InstallerNotRecorded'), [IntToStr(ResultCode), VersionFile()]), mbCriticalError, MB_OK)
  else
  begin
    InstallerSucceeded := True;
    if ResultCode = 1 then
      MsgBox(CustomMessage('InstallerWarnings'), mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunArciInstaller();
end;

{ The number after "Key": in the self-test's result file (Install-Arcilogger.ps1 writes the
  counts first, as ConvertTo-Json does with any spacing); -1 when it is not there. }
function SelfTestCount(const Content, Key: String): Integer;
var
  At, I: Integer;
  Digits: String;
begin
  Result := -1;
  At := Pos('"' + Key + '":', Content);
  if At = 0 then
    Exit;
  I := At + Length(Key) + 3;
  { No index past the end: the loops test the length first, on its own. }
  while I <= Length(Content) do
  begin
    if (Content[I] <> ' ') and (Content[I] <> #9) then
      Break;
    I := I + 1;
  end;
  Digits := '';
  while I <= Length(Content) do
  begin
    if (Content[I] < '0') or (Content[I] > '9') then
      Break;
    Digits := Digits + Content[I];
    I := I + 1;
  end;
  Result := StrToIntDef(Digits, -1);
end;

{ The finish page names the self-test's counts (0.4.0 P3) when this run wrote them and ended
  with 0 or 1; RunArciInstaller removes the file of an earlier run before it starts. }
procedure CurPageChanged(CurPageID: Integer);
var
  Content: AnsiString;
  Ok, Warn, Fail: Integer;
begin
  if (CurPageID <> wpFinished) or not InstallerSucceeded or not LoadStringFromFile(SelfTestFile(), Content) then
    Exit;
  Ok := SelfTestCount(Content, 'ok');
  Warn := SelfTestCount(Content, 'warn');
  Fail := SelfTestCount(Content, 'fail');
  if (Ok < 0) or (Warn < 0) or (Fail < 0) then
    Exit;
  WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
    FmtMessage(CustomMessage('SelfTestLine'), [IntToStr(Ok), IntToStr(Warn + Fail)]);
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
