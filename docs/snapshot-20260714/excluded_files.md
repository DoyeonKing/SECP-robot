# Excluded Files

Sensitive filenames inside evidence and face-data directories are intentionally not enumerated.

| Source | Excluded item | Size | SHA256 | Runtime requirement | Reason |
| --- | --- | ---: | --- | --- | --- |
| patrol_ai | `evidence/` | 456,029,390 bytes / 3,780 files | Not represented by one file hash | No | Live evidence images and field data |
| external FaceDB | `/home/jetson/face_db` except two included source targets | 21,703,282 bytes / 97 files | Not represented by one file hash | Identity feature only | Photos, embeddings, identity records, event data, and caches |
| patrol_ai | `yolov5s.pt` | 14,808,437 bytes | `8b3b748c1e592ddd8868022e8732fde20025197328490623cc16c6f24d0782ee` | Yes for fall detection | Model weight must remain outside Git |
| patrol_ai | `fall_evidence_test.jpg` | 282,313 bytes | `e8e61a6bfcaeb01213cfb7aac8d91e0a84ebd0adf2e22bef49643ebaa2a719a8` | No | Test evidence image |
| patrol_ai | `identity_map.json` | 380 bytes | `9f89820a60ef256b588719f14ea57752d6f28032d4ac2b8b63d6ca64c5b48065` | Identity feature only | Sensitive identity mapping |
| patrol_ai | `__pycache__/` | 59,695 bytes | Not represented by one file hash | No | Generated Python cache |
| patrol_ai | `*.bak`, `*.copy` | Multiple files | Not applicable | No | Backups are not source-of-record files |
| gateway | `gkm.wav` | 28,885,884 bytes | `9cd128e90c1d91a463f01e623a2a3a60a5c4e060ffd2b541ca1c59b0284d1b0e` | Optional entertainment asset | Audio asset, not source code |
| gateway | `song.wav` | 28,885,884 bytes | `9cd128e90c1d91a463f01e623a2a3a60a5c4e060ffd2b541ca1c59b0284d1b0e` | Optional entertainment asset | Audio asset, not source code |
| gateway | `Control_demo/patrol_ai/` | Approximately 124 KiB | Not represented by one file hash | No | Duplicate patrol source, not a gateway import |
| gateway | `__pycache__/` | Approximately 44 KiB | Not represented by one file hash | No | Generated Python cache |
| gateway | `camera_captures/` | Approximately 4 KiB | Not represented by one file hash | No | Captured field data |
| gateway | Logs, state files, gateway backups, and unimported utility scripts | Multiple files | Not applicable | No | Runtime output, backups, or independent tools outside the audited gateway dependency boundary |
| inspection bridge | `maps/yahboomcar.pgm` | 233,487 bytes | `04f8d04032270e7e295630bc4e3f50e7401fd7f643f5f6e5affe76619c134d1b` | Yes for the deployed site map | Occupancy-grid image and field data |
| inspection bridge | `maps/yahboomcar.yaml` | 125 bytes | `e0e3767cfa814b0116abba9a77bdd771485e2e84086240d5ea1ab769cefee2ff` | Paired with the deployed site map | Site-specific map metadata |
| all sources | `.git`, `.env`, private keys, tokens, passwords, container filesystems, ROS `build/`, `install/`, and `log/` | Not copied | Not applicable | No | Repository metadata, secrets, generated output, or out-of-scope runtime state |

The included `.env.example` contains only empty values. A real `.env` is excluded and must never be committed.
