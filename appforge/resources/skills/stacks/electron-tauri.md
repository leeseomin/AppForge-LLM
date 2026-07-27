# Electron/Tauri stack skill

Keep privileged native or main-process capabilities narrow. Validate every IPC message and expose a minimal typed bridge; never enable unrestricted Node access in web content. Apply content security policy, safe navigation, and explicit file-system scopes. Separate packaging success from signing and notarization. Test core logic outside the UI and smoke-test the packaged application when the platform permits.
