"""Set process limits in the child, never in a threaded preexec_fn."""
import json
import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_CPU, (3600, 3605))
os.nice(5)
command = json.loads(sys.argv[1])
if isinstance(command, str):
    os.execv("/bin/sh", ["sh", "-c", command])
os.execvp(command[0], command)
