"""One-shot provider-neutral judge worker; only the bounded return value reaches stdout."""
import importlib
import json
import os
import sys


def main():
    # Suppress SDK diagnostics before importing a caller-owned factory. No raw error output.
    output_fd = os.dup(sys.stdout.fileno())
    with open(os.devnull, 'wb') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    judge = None
    capture = None
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return 73
        request = json.loads(raw)
        if request.get('development_diagnostic') is not None:
            from data_analysis_agent.agents.review_shadow_diagnostics import FailureCapture
            settings = request['development_diagnostic']
            capture = FailureCapture(settings['directory'], settings['envelope'], request['max_output_bytes'])
        module, name = request['factory'].split(':')
        factory = getattr(importlib.import_module(module), name)
        judge = factory(**request['config'])
        if capture is not None and callable(getattr(judge,'set_diagnostic_capture',None)):
            judge.set_diagnostic_capture(capture.capture)
        result = judge.judge(request['payload'])
        if not isinstance(result, str):
            return 71
        # Bound before UTF-8 allocation and IPC. Provider must also bound its HTTP body.
        if len(result) > request['max_output_bytes']:
            return 72
        encoded = result.encode('utf-8')
        if len(encoded) > request['max_output_bytes']:
            return 72
        if capture is not None:
            try:
                if capture.raw is None:
                    capture.capture(result)
                capture.finish()
            except Exception:
                pass  # Retention failure never changes the authoritative parent audit.
        with os.fdopen(output_fd, 'wb', closefd=False) as output:
            output.write(encoded)
        return 0
    except Exception:
        if capture is not None:
            try:
                capture.finish(provider_failed=True)
            except Exception:
                pass
        return 70
    finally:
        try:
            if judge is not None and callable(getattr(judge, 'close', None)):
                judge.close()
        finally:
            os.close(output_fd)


if __name__ == '__main__':
    sys.exit(main())
