# OpenCode integration

`plugins/powercontext` contains the native PowerContext plugin for OpenCode 1.x.

OpenCode is not yet exposed by the distribution installer. The checked-in plugin remains available for development
and packaging work. After a development installation, start the Runtime and OpenCode:

```bash
powercontext server run
opencode
```

The plugin is a thin HTTP client. It does not embed storage or start the Server. It recalls bounded project context
for each user turn, captures eligible prompts as Source evidence, and exposes curated `pc_*` tools. Server failures
never block normal OpenCode work.
