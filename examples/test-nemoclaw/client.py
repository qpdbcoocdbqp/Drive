import click
import subprocess
import tempfile


@click.command()
@click.option("--message", default="Hello from Python client!", help="Message to send to the agent")
@click.option("--session-id", default="python-client", help="Session ID for the agent")
def send_agent_message(message, session_id="python-client", ssh_config_path: str=None):
    if ssh_config_path is None:
        ssh_config = subprocess.run(
            ["openshell", "sandbox", "ssh-config", "anemo"],
            capture_output=True, text=True, check=True
            ).stdout
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(ssh_config)
            ssh_config_path = f.name        
    try:
        cmd = f"nemoclaw-start openclaw agent --agent main --local -m '{message}' --session-id '{session_id}'"
        result = subprocess.run(
            ["ssh", "-T", "-F", ssh_config_path, "openshell-anemo", cmd],
            capture_output=True, text=True, timeout=120
        )          
        lines = result.stdout.split('\n')
        response_lines = [
            line for line in lines
            if not any(prefix in line for prefix in [
                "Setting up NemoClaw", "[plugins]", "(node:",
                "NemoClaw ready", "NemoClaw registered",
                "openclaw agent", "┌─", "│ ", "└─"
            ]) and line.strip()
        ]
        print('\n'.join(response_lines).strip())
        return '\n'.join(response_lines).strip()
    finally:
        import os
        os.unlink(ssh_config_path)

if __name__ == "__main__":
    send_agent_message()
