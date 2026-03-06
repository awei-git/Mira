# Mira-bridge

This folder is the message queue between the Mira iPhone app and the Mac agent, synced via iCloud Drive.

## Structure

```
Mira-bridge/
├── inbox/          # phone → mac (phone writes, mac reads)
├── outbox/         # mac → phone (mac writes, phone reads)
├── ack/            # message delivery status
├── tasks/          # background task workspaces + results
├── threads/        # conversation thread memory
├── archive/        # auto-archived old messages
└── heartbeat.json  # agent online/offline indicator
```

## Setup

1. Copy or rename this folder to `Mira-bridge/`
2. The agent will auto-create subdirectories on first run
3. In the Mira iOS app, point it to this folder in iCloud Drive

The actual `Mira-bridge/` folder is gitignored because it contains personal messages and task data.
