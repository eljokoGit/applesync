# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | ✅        |

## Reporting a vulnerability

Please do **not** open a public issue for a security flaw.

Use the **Security → Report a vulnerability** tab of the repository (GitHub
Private Vulnerability Reporting). Describe the problem, the affected version
and, if possible, how to reproduce it. You will get an answer as soon as
possible; the fix and the disclosure will be coordinated with you.

## Scope

This software reads an iOS device over USB and writes into a local folder. Of
particular interest:

- any code path that would write towards the device (none should exist: the
  device-access contract exposes no write);
- any write outside the chosen destination folder, or any overwrite of an
  existing file in the destination;
- any leak of personal data off the machine (the application only makes one
  anonymous, disableable request to the GitHub API for the version check);
- any situation where an incomplete or corrupted transfer would be reported as
  a success.

## Out of scope

- Vulnerabilities in third-party dependencies: report them upstream, and open
  a normal issue here for the version bump.
- Physical access to the machine or to an unlocked device.
