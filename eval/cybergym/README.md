# CyberGym 10-task subset

Download:
https://github.com/0xM1yu/whitehat-boss/releases/tag/cybergym-subset-10

Restore:

```bash
cat cybergym_data.zip.part-* > cybergym_data.zip
shasum -a 256 -c SHA256SUMS
unzip cybergym_data.zip
