def get_git_version():
    """
    Retrieves the current git version (tag + hash).
    """
    import subprocess
    try:
        version = subprocess.check_output(["git", "describe", "--tags", "--always"], stderr=subprocess.STDOUT).decode("utf-8").strip()
        return version
    except Exception:
        return "unknown"
