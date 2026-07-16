"""CLI entry point: ``python -m minicode``.

Starts an interactive lead session: the cron autorun thread runs in the
background while the foreground reads user input, runs an agent turn, prints
replies, and feeds the lead inbox back into the conversation.
"""

import threading

from . import bus
from . import config
from . import loop as loop_mod
from . import terminal
from .hooks import trigger_hooks
from .loop import (agent_loop, cron_autorun_loop, print_turn_assistants,
                  update_context)


def main():
    """Run the interactive lead CLI until the user types q/exit or hits Ctrl-C/EOF."""
    terminal.CLI_ACTIVE = True
    print("minicode: comprehensive agent")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    history = []
    context = update_context({}, [])
    threading.Thread(target=cron_autorun_loop,
                     args=(history, context), daemon=True).start()
    while True:
        try:
            query = input(config.PROMPT)
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", query)
        turn_start = len(history)
        history.append({"role": "user", "content": query})
        with loop_mod.agent_lock:
            agent_loop(history, context)
            context = update_context(context, history)
            print_turn_assistants(history, turn_start)

        inbox = bus.consume_lead_inbox(route_protocol=True)
        if inbox:
            def inbox_label(msg):
                req_id = msg.get("metadata", {}).get("request_id", "")
                suffix = f" req:{req_id}" if req_id else ""
                return f"{msg.get('type', 'message')}{suffix}"

            inbox_text = "\n".join(
                f"From {m['from']} [{inbox_label(m)}]: "
                f"{m['content'][:200]}" for m in inbox)
            history.append({"role": "user",
                            "content": f"[Inbox]\n{inbox_text}"})
        print()


if __name__ == "__main__":
    main()
