import statistics
import time

from fastapi.testclient import TestClient

from main import app


def percentile(values, p):
    values = sorted(values)
    index = (len(values) - 1) * p / 100
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    fraction = index - lower

    return values[lower] * (1 - fraction) + values[upper] * fraction


def benchmark_health():
    latencies = []
    total_requests = 100

    with TestClient(app) as client:
        # Warm up the application
        for _ in range(5):
            response = client.get("/health")
            assert response.status_code == 200

        # Measure response times
        for _ in range(total_requests):
            start = time.perf_counter()

            response = client.get("/health")

            elapsed_ms = (time.perf_counter() - start) * 1000

            assert response.status_code == 200
            latencies.append(elapsed_ms)

    print("\nAPI HEALTH BENCHMARK")
    print("-----------------------------")
    print(f"Requests: {total_requests}")
    print(f"Average: {statistics.mean(latencies):.2f} ms")
    print(f"p50: {percentile(latencies, 50):.2f} ms")
    print(f"p95: {percentile(latencies, 95):.2f} ms")
    print(f"Max: {max(latencies):.2f} ms")


if __name__ == "__main__":
    benchmark_health()
