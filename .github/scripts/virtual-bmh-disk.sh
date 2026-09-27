#!/usr/bin/env bash

# Verify that a virtual BMH VM is using the disk created for it and that the
# active libvirt device exposes the expected capacity. This must use virsh,
# rather than qemu-img, while the VM is running.
verify_virtual_bmh_disk() {
  if [[ "$#" -ne 3 ]]; then
    printf 'verify_virtual_bmh_disk requires VM name, expected disk path, and minimum capacity bytes\n' >&2
    return 2
  fi

  local vm_name="$1"
  local expected_path="$2"
  local minimum_capacity_bytes="$3"
  local block_list
  local actual_path
  local block_info
  local capacity_bytes
  local minimum_capacity_gib

  if [[ ! "$minimum_capacity_bytes" =~ ^[0-9]+$ ]]; then
    printf 'Invalid minimum virtual disk capacity: %s\n' "$minimum_capacity_bytes" >&2
    return 2
  fi

  if ! block_list=$(virsh -c qemu:///system domblklist "$vm_name" --details 2>&1); then
    printf 'Failed to list disks for virtual BMH VM %s: %s\n' "$vm_name" "$block_list" >&2
    return 1
  fi

  actual_path=$(awk '$3 == "vda" {print $4; exit}' <<< "$block_list")
  if [[ -z "$actual_path" ]]; then
    printf 'Virtual BMH VM %s has no active vda disk (expected %s)\n' "$vm_name" "$expected_path" >&2
    return 1
  fi
  if [[ "$actual_path" != "$expected_path" ]]; then
    printf 'Virtual BMH VM %s uses vda source %s; expected %s\n' \
      "$vm_name" "$actual_path" "$expected_path" >&2
    return 1
  fi

  if ! block_info=$(virsh -c qemu:///system domblkinfo "$vm_name" vda 2>&1); then
    printf 'Failed to inspect vda capacity for virtual BMH VM %s: %s\n' "$vm_name" "$block_info" >&2
    return 1
  fi

  capacity_bytes=$(awk '$1 == "Capacity:" {print $2; exit}' <<< "$block_info")
  if [[ ! "$capacity_bytes" =~ ^[0-9]+$ ]]; then
    printf 'Could not determine vda capacity for virtual BMH VM %s from: %s\n' \
      "$vm_name" "$block_info" >&2
    return 1
  fi
  if (( capacity_bytes < minimum_capacity_bytes )); then
    minimum_capacity_gib=$((minimum_capacity_bytes / 1024 / 1024 / 1024))
    printf 'Virtual BMH VM %s disk %s is only %s bytes; expected at least %s bytes (%s GiB)\n' \
      "$vm_name" "$actual_path" "$capacity_bytes" "$minimum_capacity_bytes" "$minimum_capacity_gib" >&2
    return 1
  fi

  printf 'Verified virtual BMH VM %s vda: %s bytes at %s\n' \
    "$vm_name" "$capacity_bytes" "$actual_path"
}
