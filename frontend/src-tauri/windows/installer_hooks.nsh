!macro AICC_DETECT_VC_RUNTIME
  StrCpy $R4 1
  ReadRegDWORD $R0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
  ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Version"
  ${If} $R0 == 1
    StrCpy $R1 $R1 "" 1
    ${VersionCompare} "$R1" "14.52.37029.0" $R2
    ${If} $R2 < 2
      StrCpy $R4 0
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  SetRegView 64
  !insertmacro AICC_DETECT_VC_RUNTIME
  ${If} $R4 == 1
    DetailPrint "Installing the Microsoft Visual C++ 2015-2022 x64 runtime prerequisite..."
    ExecWait '"$INSTDIR\resources\vc-redist\vc_redist.x64.exe" /install /passive /norestart /log "$TEMP\AI-Career-Copilot-vcredist-install.log"' $R3
    ${If} $R3 != 0
      ${AndIf} $R3 != 3010
      Delete "$INSTDIR\resources\vc-redist\vc_redist.x64.exe"
      MessageBox MB_ICONSTOP|MB_OK "The Microsoft Visual C++ x64 runtime could not be installed (exit code $R3). Setup copied the application files but will close without launching the app. Review the Visual C++ setup log in your temporary folder, resolve the issue, then rerun this installer with administrator approval. Your application data was not changed."
      Quit
    ${EndIf}
    !insertmacro AICC_DETECT_VC_RUNTIME
    ${If} $R4 == 1
      Delete "$INSTDIR\resources\vc-redist\vc_redist.x64.exe"
      MessageBox MB_ICONSTOP|MB_OK "The Microsoft Visual C++ x64 runtime setup finished, but the required runtime is still unavailable. Setup copied the application files but will close without launching the app. Review the Visual C++ setup log in your temporary folder, resolve the issue, then rerun this installer. Your application data was not changed."
      Quit
    ${EndIf}
    ${If} $R3 == 3010
      SetRebootFlag true
      MessageBox MB_ICONINFORMATION|MB_OK "The Visual C++ runtime was installed. Windows reports that a restart may be needed before AI Career Copilot can start."
    ${EndIf}
  ${EndIf}
  Delete "$INSTDIR\resources\vc-redist\vc_redist.x64.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\installer\close_running_app.ps1" "$INSTDIR\ai-career-copilot-desktop.exe"' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "AI Career Copilot did not close cleanly. Close the app and run the installer again. Your saved data was preserved."
    Abort
  ${EndIf}
!macroend
