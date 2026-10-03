"""The state commit-back, exercised in throwaway git repositories.

All three cases the workflow depends on:
  1. a new state.json commits and pushes,
  2. nothing staged exits 0 without inventing an empty commit,
  3. the branch moves under us mid-run, and the rejected push rebases and
     keeps both commits.
"""

from harness import check, done, equal, heading
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "commit_state.sh")

ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="test",
    GIT_AUTHOR_EMAIL="test@example.com",
    GIT_COMMITTER_NAME="test",
    GIT_COMMITTER_EMAIL="test@example.com",
    GIT_CONFIG_GLOBAL="/dev/null",
    GIT_CONFIG_SYSTEM="/dev/null",
    ATTEMPTS="4",
    SLEEP_BASE="0",
    BRANCH="main",
)


def git(cwd, *args, check_rc=True):
    result = subprocess.run(("git",) + args, cwd=cwd, env=ENV, capture_output=True, text=True)
    if check_rc and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def run_script(cwd):
    return subprocess.run(["bash", SCRIPT, "state.json"], cwd=cwd, env=ENV,
                          capture_output=True, text=True)


def fresh():
    """A bare origin with one commit on main, plus a working clone."""
    tmp = tempfile.mkdtemp()
    origin = os.path.join(tmp, "origin.git")
    git(tmp, "init", "--bare", "--initial-branch=main", origin)
    work = os.path.join(tmp, "work")
    git(tmp, "clone", origin, work)
    open(os.path.join(work, "README.md"), "w").write("# test\n")
    git(work, "add", "README.md")
    git(work, "commit", "-m", "initial")
    git(work, "push", "origin", "main")
    return tmp, origin, work


def log(cwd, ref="origin/main"):
    return git(cwd, "log", "--format=%s", ref).splitlines()


heading("case 1: a new state.json commits and pushes")
tmp, origin, work = fresh()
open(os.path.join(work, "state.json"), "w").write('{"schema": 1, "last_run": "2026-10-03"}\n')
result = run_script(work)
equal("exit 0", result.returncode, 0, )
check("it pushed", "pushed to main" in result.stdout, result.stdout + result.stderr)
git(work, "fetch", "origin", "main")
subjects = log(work)
equal("origin/main has the state commit", len(subjects), 2)
check("with a sensible message", subjects[0].startswith("state: weekly run"), subjects[0])
committed = git(work, "show", "origin/main:state.json")
check("and the file content is really there", "2026-10-03" in committed, committed)
shutil.rmtree(tmp)

heading("case 2: nothing to commit exits 0 and does not fail the job")
tmp, origin, work = fresh()
open(os.path.join(work, "state.json"), "w").write('{"schema": 1}\n')
run_script(work)
before = git(work, "rev-parse", "HEAD")
result = run_script(work)  # same content, second time
equal("exit 0", result.returncode, 0)
check("it said so", "nothing to commit" in result.stdout, result.stdout + result.stderr)
equal("no empty commit was created", git(work, "rev-parse", "HEAD"), before)
shutil.rmtree(tmp)

heading("case 2b: no state.json at all exits 0")
tmp, origin, work = fresh()
result = run_script(work)
equal("exit 0", result.returncode, 0)
check("it said so", "nothing to do" in result.stdout, result.stdout + result.stderr)
shutil.rmtree(tmp)

heading("case 3: main moves between checkout and push")
tmp, origin, work = fresh()
# Somebody else pushes to main while this run is in flight.
racer = os.path.join(tmp, "racer")
git(tmp, "clone", origin, racer)
open(os.path.join(racer, "NOTES.md"), "w").write("someone else was here\n")
git(racer, "add", "NOTES.md")
git(racer, "commit", "-m", "a commit that landed first")
git(racer, "push", "origin", "main")

open(os.path.join(work, "state.json"), "w").write('{"schema": 1, "last_run": "2026-10-10"}\n')
result = run_script(work)
equal("exit 0 after the retry", result.returncode, 0, )
check("it noticed the race", "push rejected" in result.stdout,
      result.stdout + result.stderr)
check("and rebased rather than forcing", "Rebasing" in result.stdout,
      result.stdout + result.stderr)
git(work, "fetch", "origin", "main")
subjects = log(work)
equal("three commits on origin/main", len(subjects), 3)
check("the state commit is on top", subjects[0].startswith("state: weekly run"), str(subjects))
check("the other commit survived", "a commit that landed first" in subjects, str(subjects))
check("both files are present",
      "someone else was here" in git(work, "show", "origin/main:NOTES.md")
      and "2026-10-10" in git(work, "show", "origin/main:state.json"))
shutil.rmtree(tmp)

done()
