# CaaS Virtual BMH Disk Capacity Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Ensure each fresh CaaS/BMaaS virtual BMH presents a verified 120 GiB disk to Metal3/Assisted and never silently reuses an undersized prior VM.

**Architecture:** The test-infra BMH helper will fail closed on clone-scoped leftovers, create a 120G qcow2 disk, and verify the active libvirt `vda` source and capacity before creating the BMH CR. The local remote-provisioning wrapper will resolve a dedicated fork branch to an immutable commit SHA; fresh-only policy will reject resume requests and leave existing resources for separately confirmed teardown. No shared host is contacted during implementation.

**Tech Stack:** Bash, libvirt/virsh, qemu-img, OpenShift `oc`, Git.

---

### Task 1: Make virtual BMH disk setup verifiable

**Files:**
- Modify: `.github/scripts/setup-virtual-bmh.sh`

**Steps:**
1. Preserve the current requirement of a 120G qcow2 disk and add a constant for its expected virtual capacity (`120 * 1024^3` bytes).
2. Before side effects, refuse pre-existing clone-specific disk directories, libvirt pool/domains, sushy state, BMHs, or BMC secrets; do not delete or overwrite retained resources.
3. After each VM starts, verify `virsh domblklist --details` maps `vda` to the expected qcow2 path and `virsh domblkinfo` reports at least 120 GiB. On mismatch, print VM/path/actual/required capacity and stop before applying its BMH.
4. Run `bash -n`, ShellCheck if installed, and a temporary offline mock check for both undersized and adequate `domblkinfo` responses. Do not add tests to this repository; its AGENTS.md directs test changes to the osac mono-repo.

### Task 2: Make remote provisioning fresh-only and pin the helper branch

**Files:**
- Modify (workspace-local, not part of the test-infra fork): `tools/remote-caas-provision.sh`
- Modify (workspace-local, not part of the test-infra fork): `tools/remote-caas-host.sh`

**Steps:**
1. Resolve the dedicated `fix/caas-bmh-disk-capacity` fork branch to a full commit SHA locally and pass that SHA to the remote host; keep the host checkout detached/pinned to the SHA.
2. Reject a non-empty `RESUME_RUN_DIR` at both entry points so old BMH VMs cannot bypass the updated helper. Keep the existing collision checks; never automatically clean previous run resources.
3. Verify only by syntax checks and offline input/ref-resolution checks; do not invoke SSH or provision the shared environment.

### Task 3: Commit and publish the implementation

**Files:**
- Commit the helper change and this plan on `fix/caas-bmh-disk-capacity`.

**Steps:**
1. Review the staged diff and run final offline checks.
2. Commit with DCO sign-off and the required `Assisted-by:` trailer.
3. Push only to the resolved fork remote and confirm the branch's full SHA is retrievable; the local provisioning wrapper must resolve and display that SHA before a future run.
