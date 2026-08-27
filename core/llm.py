import logging
from ollama import Client
from core.tools import ALL_TOOLS, TOOLS_BY_NAME

log = logging.getLogger("secretary.llm")

BASE_SYSTEM_PROMPT = """Ты голосовой секретарь по имени Луна. Отвечай КОРОТКО одним-двумя предложениями простым текстом. НЕ используй LaTeX, формулы, символы $, markdown, эмодзи. НЕ размышляй вслух. Если вопрос требует актуальной информации (погода, курсы, новости, списки, время) — вызывай подходящий инструмент вместо того чтобы отвечать по памяти.

ВАЖНО про опасные действия (выключение, перезагрузка, установка приложений): когда ты вызываешь такой инструмент, он НЕ выполняется сразу — создаётся запрос на подтверждение пользователя. Тебе придёт сообщение что действие ожидает подтверждения. Озвучь пользователю что именно будет сделано и попроси сказать 'да' для подтверждения или 'нет' для отмены. НИКОГДА не предполагай что пользователь согласился и не вызывай инструмент повторно — подтверждение даёт только пользователь своим голосом.

ВАЖНО про память: если пользователь сообщает важный факт о себе (предпочтения, важные даты, повторяющиеся дела, личные детали) — вызови remember_fact, даже если он явно не попросил 'запомни'. Если вопрос может зависеть от того, что ты уже знаешь о пользователе — сначала вызови recall_facts."""

class LLM:
    def __init__(self, cfg, memory=None, personality=None):
        self.fast = cfg["fast"]
        self.smart = cfg["smart"]
        self.url = cfg.get("ollama_url", "http://localhost:11434")
        self.client = Client(host=self.url)
        self.memory = memory
        self.personality = personality

    def _build_system_prompt(self):
        prompt = BASE_SYSTEM_PROMPT
        if self.personality is not None:
            prompt += f"\n\n{self.personality.get_system_prompt_fragment()}"
        if self.memory is not None:
            facts = self.memory.get_facts()
            if facts:
                facts_block = "\n".join(f"- {f}" for f in facts[-10:])
                prompt += f"\n\nЧто уже известно о пользователе:\n{facts_block}"
        return prompt

    def _build_messages(self, question, include_history):
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        if include_history and self.memory is not None:
            for turn in self.memory.dialog_buffer[-10:]:
                role = "user" if turn["role"] == "user" else "assistant"
                messages.append({"role": role, "content": turn["text"]})
        messages.append({"role": "user", "content": question})
        return messages

    def ask(self, model, question, max_tool_hops=6, include_history=True):
        messages = self._build_messages(question, include_history)
        try:
            for _hop in range(max_tool_hops):
                response = self.client.chat(
                    model=model,
                    messages=messages,
                    tools=ALL_TOOLS,
                    options={
                        "temperature": 0.3,
                        "num_predict": 300,
                        # GTX 1650 (4GB VRAM) не вмещает qwen3:8b целиком
                        # (веса ~5-6GB в Q4 + KV cache) — ни num_gpu=99
                        # (падал с OOM), ни отсутствие num_gpu (ушло в
                        # 100% CPU, ответ занял ~2 минуты) не были верным
                        # решением. Нужна ЧАСТИЧНАЯ выгрузка: явное число
                        # слоёв, которое реально влезает, плюс урезанный
                        # контекст, чтобы освободить место под эти слои.
                        # 20 слоёв — стартовая точка, подбирается опытным
                        # путём через `ollama ps` (смотри % GPU/CPU) и
                        # `nvidia-smi` во время генерации.
                        "num_gpu": 20,
                        "num_ctx": 2048,
                        "num_batch": 256,
                    },
                )
                msg = response["message"]
                messages.append(msg)
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    answer = (msg.get("content") or "").strip()
                    final = self.clean_response(answer) if answer else "Не понял вопрос."
                    if self.memory is not None and include_history:
                        self.memory.add_to_dialog("user", question)
                        self.memory.add_to_dialog("assistant", final)
                    return final
                for call in tool_calls:
                    name = call["function"]["name"]
                    args = call["function"].get("arguments", {}) or {}
                    log.info(f"Вызван инструмент {name}({args})")
                    fn = TOOLS_BY_NAME.get(name)
                    if fn is None:
                        result = f"Неизвестный инструмент: {name}"
                    else:
                        try:
                            result = fn(**args)
                        except Exception as e:
                            log.error(f"Ошибка выполнения {name}: {e}")
                            result = f"Ошибка при выполнении: {e}"
                    messages.append({"role": "tool", "content": str(result)})
            log.error("Превышен лимит последовательных вызовов инструментов")
            return "Не смог обработать запрос за разумное число шагов."
        except Exception as e:
            log.error(f"Ошибка: {e}")
            return "Произошла ошибка."

    def clean_response(self, text):
        text = text.replace("Thinking...", "").replace("...done thinking.", "")
        text = text.replace("$$", "").replace("$", "").replace("\\", "")
        text = text.replace("{", "").replace("}", "")
        return " ".join(text.split()).strip()

    def unload(self, model):
        try:
            self.client.chat(model=model, messages=[], keep_alive=0)
        except Exception:
            pass

    def is_complex(self, question):
        prompt = ("Определи, требует ли этот вопрос глубокого экспертного ответа "
                  "или это простой вопрос. Вопрос: '" + question + "'. "
                  "Ответь одним словом: СЛОЖНЫЙ или ПРОСТОЙ.")
        response = self.ask(self.fast, prompt, include_history=False)
        return "СЛОЖНЫЙ" in response.upper()
