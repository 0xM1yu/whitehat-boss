# Security policy

**English** · [한국어](SECURITY.ko.md)

## Reporting a vulnerability in this repository

Do **not** open a public issue for a security problem in the harness itself (for example, a path by which a
task bundle or model output crosses a trust boundary and runs on the host). Report it privately through the
repository's **Security tab → Report a vulnerability**. Include reproduction steps, the affected commit, and
what you observed.

The security contact is [@danielcho02](https://github.com/danielcho02). If the Security tab has no such
button, open a public issue that mentions @danielcho02 and says only "security inquiry", with no details —
the contact will open a private channel.

## Responsible use

This tool looks for vulnerability candidates in C/C++ code and **builds inputs that cause crashes (PoCs).**

- **Use it only on targets you are authorized to test.** That covers your own code, code you have explicit
  permission to test, and benchmarks built to reproduce known vulnerabilities, such as CyberGym.
- **Run PoCs in an isolated environment.** The real-serving profiles (`harness_pool`, `harness_pool_hybrid`)
  run tools and PoCs inside Docker containers; Singularity is used by the development profile
  `dev_singularity` ([tools/README.md](tools/README.md)). Do not run generated inputs directly on a host
  outside a container.
- **If you find a new vulnerability in third-party open source, follow Coordinated Vulnerability
  Disclosure.** Report it privately to the maintainers first, following that project's security policy, and
  do not publish details or PoCs before a fix is available.

## Out of scope

The vulnerabilities in CyberGym tasks are already known. Report problems with the tasks themselves to the
CyberGym project.
