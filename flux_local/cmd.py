import subprocess


def run(command, **kwargs):
    """Runs a command in the shell and returns the results.

    This function runs a command in the shell and returns the results. The results
    include the stdout, stderr, and return code from the command.

    Keyword Arguments:
        command {str} -- The command to run in the shell

    Returns:
        dict -- Dictionary with the fields stdout, stderr, and returncode
    """
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs
    )
    return {
        "stdout": result.stdout.decode("utf-8"),
        "stderr": result.stderr.decode("utf-8"),
        "returncode": result.returncode,
    }
