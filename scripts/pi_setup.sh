#!/bin/bash
# ============================================================
# Smart AI Companion — Raspberry Pi Setup & Benchmark Script
# ============================================================
#
# This script:
#   1. Collects hardware information
#   2. Installs Ollama (if not installed)
#   3. Pulls the Llama 3.2 1B model
#   4. Runs direct CLI tests
#   5. Runs a reproducible benchmark
#
# Usage:
#   chmod +x scripts/pi_setup.sh
#   ./scripts/pi_setup.sh
#
# Run on the Raspberry Pi, NOT on the development machine.
# ============================================================

set -e

MODEL="llama3.2:1b"
BENCHMARK_FILE="benchmark_results.txt"

echo "============================================================"
echo " Smart AI Companion — Pi Setup & Benchmark"
echo "============================================================"
echo ""

# ── Step 1: Hardware Information ──────────────────────────────

echo "── STEP 1: Hardware Information ──"
echo ""

echo "Hostname: $(hostname)"
echo "Date: $(date)"
echo ""

echo "--- CPU ---"
cat /proc/cpuinfo | grep -E "^(model name|Hardware|Revision|Serial)" | head -6
echo "CPU cores: $(nproc)"
echo ""

echo "--- Memory ---"
free -h
echo ""

echo "--- Storage ---"
df -h / | tail -1
echo ""

echo "--- OS ---"
cat /etc/os-release | grep -E "^(PRETTY_NAME|VERSION_ID)"
uname -m
echo ""

echo "--- Temperature ---"
if command -v vcgencmd &> /dev/null; then
    vcgencmd measure_temp
else
    echo "vcgencmd not available"
    cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null && echo "(millidegrees C)" || echo "Temperature: N/A"
fi
echo ""

echo "--- Pi Model ---"
cat /proc/device-tree/model 2>/dev/null || echo "Not a Raspberry Pi or model info unavailable"
echo ""

# ── Step 2: Install Ollama ────────────────────────────────────

echo "── STEP 2: Install Ollama ──"
echo ""

if command -v ollama &> /dev/null; then
    echo "Ollama is already installed:"
    ollama --version
else
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo ""
    echo "Ollama installed:"
    ollama --version
fi
echo ""

# Ensure Ollama service is running
echo "Checking Ollama service..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Ollama service is running."
else
    echo "Starting Ollama service..."
    ollama serve &
    sleep 3
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "Ollama service started."
    else
        echo "ERROR: Could not start Ollama service."
        exit 1
    fi
fi
echo ""

# ── Step 3: Pull Model ───────────────────────────────────────

echo "── STEP 3: Pull Model ($MODEL) ──"
echo ""

ollama pull "$MODEL"
echo ""
echo "Installed models:"
ollama list
echo ""

# ── Step 4: Direct CLI Tests ─────────────────────────────────

echo "── STEP 4: Direct CLI Tests ──"
echo ""

PROMPTS=(
    "Hello"
    "What is Raspberry Pi?"
    "Explain IoT in one sentence."
    "What is 2 plus 2?"
)

for prompt in "${PROMPTS[@]}"; do
    echo "--- Prompt: \"$prompt\" ---"
    START=$(date +%s%N)
    RESPONSE=$(ollama run "$MODEL" "$prompt" 2>&1)
    END=$(date +%s%N)
    ELAPSED=$(( (END - START) / 1000000 ))
    echo "Response: $RESPONSE"
    echo "Latency: ${ELAPSED}ms"
    echo ""

    # Check temperature and memory after each prompt
    if command -v vcgencmd &> /dev/null; then
        echo "Temperature: $(vcgencmd measure_temp)"
    fi
    echo "Memory: $(free -h | grep Mem | awk '{print $3 "/" $2}')"
    echo ""
done

# ── Step 5: Benchmark ────────────────────────────────────────

echo "── STEP 5: Benchmark ──"
echo ""
echo "Running benchmark with controlled prompts..."
echo ""

# Benchmark prompts (short, controlled)
BENCH_PROMPTS=(
    "Explain what an API is in 2 sentences."
    "What are the benefits of edge computing?"
    "Write a Python function that adds two numbers."
    "What is the difference between RAM and storage?"
    "Describe the purpose of a web server in one paragraph."
)

{
    echo "============================================================"
    echo " Smart AI Companion — Benchmark Results"
    echo " Model: $MODEL"
    echo " Date: $(date)"
    echo " Hardware: $(cat /proc/device-tree/model 2>/dev/null || echo 'Unknown')"
    echo " RAM: $(free -h | grep Mem | awk '{print $2}')"
    echo " OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
    echo " Arch: $(uname -m)"
    echo "============================================================"
    echo ""

    TOTAL_LATENCY=0
    COUNT=0

    for prompt in "${BENCH_PROMPTS[@]}"; do
        COUNT=$((COUNT + 1))
        echo "--- Test $COUNT: \"$prompt\" ---"

        # Use the API for more detailed metrics
        START=$(date +%s%N)
        RESULT=$(curl -s http://localhost:11434/api/chat -d "{
            \"model\": \"$MODEL\",
            \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}],
            \"stream\": false
        }" 2>&1)
        END=$(date +%s%N)

        WALL_MS=$(( (END - START) / 1000000 ))
        TOTAL_LATENCY=$((TOTAL_LATENCY + WALL_MS))

        # Parse response
        CONTENT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',{}).get('content','ERROR'))" 2>/dev/null || echo "PARSE_ERROR")
        EVAL_COUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('eval_count',0))" 2>/dev/null || echo "0")
        EVAL_DURATION=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin).get('eval_duration',0); print(round(d/1e6))" 2>/dev/null || echo "0")
        TOTAL_DURATION=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin).get('total_duration',0); print(round(d/1e6))" 2>/dev/null || echo "0")

        echo "Wall time: ${WALL_MS}ms"
        echo "Eval tokens: $EVAL_COUNT"
        echo "Eval duration: ${EVAL_DURATION}ms"
        echo "Total duration: ${TOTAL_DURATION}ms"
        if [ "$EVAL_DURATION" -gt 0 ] && [ "$EVAL_COUNT" -gt 0 ]; then
            TPS=$(python3 -c "print(round($EVAL_COUNT / ($EVAL_DURATION / 1000), 1))" 2>/dev/null || echo "N/A")
            echo "Tokens/sec: $TPS"
        fi
        echo "Response: ${CONTENT:0:200}..."
        echo ""
    done

    AVG_LATENCY=$((TOTAL_LATENCY / COUNT))
    echo "============================================================"
    echo " Summary"
    echo "============================================================"
    echo "Prompts tested: $COUNT"
    echo "Total wall time: ${TOTAL_LATENCY}ms"
    echo "Average wall time: ${AVG_LATENCY}ms"
    echo ""

    # System stats
    echo "--- System State After Benchmark ---"
    if command -v vcgencmd &> /dev/null; then
        echo "Temperature: $(vcgencmd measure_temp)"
    fi
    echo "Memory:"
    free -h | grep -E "^(Mem|Swap)"
    echo ""
    echo "CPU load:"
    uptime
    echo ""

} | tee "$BENCHMARK_FILE"

echo ""
echo "Benchmark results saved to: $BENCHMARK_FILE"
echo ""
echo "============================================================"
echo " Setup complete!"
echo ""
echo " To use with Django, set in your .env:"
echo "   AI_ENGINE=local"
echo "   OLLAMA_HOST=http://localhost:11434"
echo "   OLLAMA_MODEL=$MODEL"
echo "============================================================"
