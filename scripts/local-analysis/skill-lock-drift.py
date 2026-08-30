#!/usr/bin/env python3

import argparse
import hashlib
import json
import ntpath
import os
import posixpath
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

LOCK_VERSION = 3
MAX_LOCK_BYTES = 1024 * 1024
MAX_TREE_DEPTH = 128
TREE_HASH_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class VerificationError(Exception):
  pass


@dataclass(frozen=True)
class LockEntry:
  label: str
  directory: str
  expected_hash: str


def sanitize_name(name):
  sanitized = re.sub(r"[^a-z0-9._]+", "-", name.lower())
  sanitized = re.sub(r"^[.\-]+|[.\-]+$", "", sanitized)
  return sanitized[:255] or "unnamed-skill"


def load_lock(lock_file):
  flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
  descriptor = None
  try:
    descriptor = os.open(lock_file, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LOCK_BYTES:
      raise VerificationError
    handle = os.fdopen(descriptor, encoding="utf-8")
    descriptor = None
    with handle:
      data = json.load(handle)
  except VerificationError:
    raise
  except (OSError, UnicodeError, ValueError) as error:
    raise VerificationError from error
  finally:
    if descriptor is not None:
      try:
        os.close(descriptor)
      except OSError:
        pass

  if not isinstance(data, dict) or data.get("version") != LOCK_VERSION:
    raise VerificationError
  skills = data.get("skills")
  if not isinstance(skills, dict):
    raise VerificationError

  entries = []
  directories = set()
  for name, value in skills.items():
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or "\0" in name:
      raise VerificationError
    if not isinstance(value, dict):
      raise VerificationError
    expected_hash = value.get("skillFolderHash")
    if expected_hash == "":
      continue
    if not isinstance(expected_hash, str) or not TREE_HASH_PATTERN.fullmatch(expected_hash):
      raise VerificationError
    directory = sanitize_name(name)
    if directory == "unnamed-skill" and name != directory:
      raise VerificationError
    if directory in directories:
      raise VerificationError
    directories.add(directory)
    entries.append(LockEntry(directory, directory, expected_hash))
  return tuple(sorted(entries, key=lambda entry: entry.directory.encode()))


def object_hash(kind, content):
  header = kind + b" " + str(len(content)).encode() + b"\0"
  return hashlib.sha1(header + content).digest()


def read_file(directory_fd, name):
  flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
  try:
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as handle:
      mode = os.fstat(handle.fileno()).st_mode
      if not stat.S_ISREG(mode):
        raise VerificationError
      return mode, handle.read()
  except OSError as error:
    raise VerificationError from error


def symlink_stays_within_root(relative_directory, target):
  if posixpath.isabs(target) or ntpath.isabs(target):
    return False
  normalized_target = target.replace("\\", "/")
  joined = posixpath.normpath(posixpath.join(relative_directory, normalized_target))
  return joined != ".." and not joined.startswith("../")


def tree_hash(directory_fd, relative_directory="", depth=0):
  if depth > MAX_TREE_DEPTH:
    raise VerificationError
  try:
    names = os.listdir(directory_fd)
  except OSError as error:
    raise VerificationError from error

  entries = []
  for name in names:
    try:
      metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as error:
      raise VerificationError from error
    encoded_name = os.fsencode(name)
    child_relative = posixpath.join(relative_directory, name)

    if stat.S_ISDIR(metadata.st_mode):
      flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
      try:
        child_fd = os.open(name, flags, dir_fd=directory_fd)
      except OSError as error:
        raise VerificationError from error
      try:
        child_hash, child_has_entries = tree_hash(child_fd, child_relative, depth + 1)
      finally:
        os.close(child_fd)
      if child_has_entries:
        entries.append((encoded_name, True, b"40000", child_hash))
    elif stat.S_ISREG(metadata.st_mode):
      file_mode, content = read_file(directory_fd, name)
      mode = b"100755" if file_mode & 0o111 else b"100644"
      entries.append((encoded_name, False, mode, object_hash(b"blob", content)))
    elif stat.S_ISLNK(metadata.st_mode):
      try:
        target = os.readlink(name, dir_fd=directory_fd)
      except OSError as error:
        raise VerificationError from error
      if not symlink_stays_within_root(relative_directory, target):
        raise VerificationError
      entries.append((encoded_name, False, b"120000", object_hash(b"blob", os.fsencode(target))))
    else:
      raise VerificationError

  entries.sort(key=lambda entry: entry[0] + (b"/" if entry[1] else b""))
  content = b"".join(mode + b" " + name + b"\0" + digest for name, _, mode, digest in entries)
  return object_hash(b"tree", content), bool(entries)


def hash_skill(root_fd, directory):
  try:
    metadata = os.stat(directory, dir_fd=root_fd, follow_symlinks=False)
  except FileNotFoundError:
    return None
  except OSError as error:
    raise VerificationError from error
  if not stat.S_ISDIR(metadata.st_mode):
    raise VerificationError

  flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
  try:
    directory_fd = os.open(directory, flags, dir_fd=root_fd)
  except OSError as error:
    raise VerificationError from error
  try:
    digest, _ = tree_hash(directory_fd)
    return digest.hex()
  finally:
    os.close(directory_fd)


def verify(skills_dir, entries):
  try:
    canonical_root = Path(skills_dir).resolve(strict=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(canonical_root, flags)
  except OSError as error:
    raise VerificationError from error

  findings = []
  try:
    for entry in entries:
      actual_hash = hash_skill(root_fd, entry.directory)
      if actual_hash is None:
        findings.append(f"- ⚠️ **{entry.label}** — lock 有紀錄，但已安裝 skill 目錄不存在")
      elif actual_hash != entry.expected_hash:
        findings.append(f"- ⚠️ **{entry.label}** — installed skill 與 lock hash 不符（只通報、不自動修復）")
  finally:
    os.close(root_fd)
  return findings


def parse_args(argv):
  parser = argparse.ArgumentParser()
  parser.add_argument("--skills-dir", required=True)
  parser.add_argument("--lock-file", required=True)
  return parser.parse_args(argv)


def main(argv):
  args = parse_args(argv)
  try:
    entries = load_lock(args.lock_file)
    findings = verify(args.skills_dir, entries)
  except (VerificationError, RecursionError):
    print("- ⚠️ installed skill lock 驗證失敗 — lock schema、entry 或 skill 路徑不安全")
    return 2
  print("\n".join(findings))
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
