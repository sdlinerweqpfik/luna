import logging
from ollama import Client
from core.tools import ALL_TOOLS, TOOLS_BY_NAME
from core.context_manager import build_context
from core import security_gateway
from core import diagnostics

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

    def _build_messages(self, question, include_history):
        system_prompt = BASE_SYSTEM_PROMPT
        if self.personality is not None:
            try:
                system_prompt += f"\n\n{self.personality.get_system_prompt_fragment()}"
            except Exception:
                pass

        messages = [{"role": "system", "content": system_prompt}]

        dialog_buf = None
        if include_history and self.memory is not None:
            dialog_buf = self.memory.dialog_buffer

        # Diagnostics: событие построения контекста (счётчики, без содержимого)
        with diagnostics.span("context") as _sp:
            ctx = build_context(
                request=question,
                memory=self.memory,
                personality=None,
                dialog_buffer=dialog_buf,
            )
            _sp.update(
                core=len(ctx.core_memory),
                relevant=len(ctx.relevant_memory),
                dialog=len(ctx.recent_dialog),
            )

        messages.extend(ctx.to_messages())
        messages.append({"role": "user", "content": question})
        return messages

    def ask(self, model, question, max_tool_hops=6, include_history=True):
        messages = self._build_messages(question, include_history)
        try:
            for _hop in range(max_tool_hops):
                # Diagnostics: вызов модели
                with diagnostics.span("llm", model=model, hop=_hop):
                    response = self.client.chat(
                        model=model,
                        messages=messages,
                        tools=ALL_TOOLS,
                        options={
                            "temperature": 0.3,
                            "num_predict": 300,
                            "num_gpu": 12,
                            "num_ctx": 4096,
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
                    # Diagnostics: tool call (ALLOW/DENY ловит мост в diagnostics)
                    with diagnostics.span("tool", tool=name) as _sp:
                        try:
                            result = security_gateway.execute(
                                name=name, fn=fn, args=args
                            )
                        except Exception as e:
                            _sp.update(success=False, error=type(e).__name__)
                            log.error(f"Ошибка выполнения {name}: {e}")
                            result = f"Ошибка при выполнении: {e}"
                    messages.append({"role": "tool", "content": str(result)})
            log.error("Превышен лимит последовательных вызовов инструментов")
            return "Не смогла обработать запрос за разумное число шагов."
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


class KidsLLM:
    """Упрощённая Луна для детей: только безопасные инструменты."""
    
    def __init__(self, cfg, memory=None, personality=None):
        self.fast = "qwen2.5:3b"
        self.smart = "qwen2.5:3b"
        self.url = cfg.get("ollama_url", "http://localhost:11434")
        self.client = Client(host=self.url)
        self.memory = memory
        self.personality = personality
        
        from core.kids_tools import KIDS_TOOLS_BY_NAME, KIDS_ALLOWED_TOOLS
        self.kids_tools_by_name = KIDS_TOOLS_BY_NAME
        self.kids_allowed_tools = KIDS_ALLOWED_TOOLS
        
        self.kids_system_prompt = """Ты детская версия помощника Луны. 
        Отвечай коротко и дружелюбно. НЕ выполняй команды управления компьютером.
        Если просят выключить/открыть/установить что-то — вежливо откажи."""

    def clean_response(self, text):
        text = text.replace("Thinking...", "").replace("...done thinking.", "")
        text = text.replace("$$", "").replace("$", "").replace("\\", "")
        return " ".join(text.split()).strip()

    def unload(self, model):
        try:
            self.client.chat(model=model, messages=[], keep_alive=0)
        except Exception:
            pass

    def ask(self, model, question, max_tool_hops=6, include_history=False):
        """Упрощённый ask без диалоговой истории."""
        messages = [
            {"role": "system", "content": self.kids_system_prompt},
            {"role": "user", "content": question}
        ]
        
        try:
            # Проверяем, есть ли модель
            try:
                self.client.chat(model=model, messages=[{"role": "user", "content": "привет"}], options={"num_predict": 5})
            except Exception as e:
                log.error(f"KidsLLM: модель {model} недоступна: {e}")
                return "Ошибка: модель не загружена. Установи: ollama pull qwen2.5:3b"
            
            for _hop in range(max_tool_hops):
                response = self.client.chat(
                    model=model,
                    messages=messages,
                    tools=[t for t in ALL_TOOLS if t.__name__ in self.kids_allowed_tools],
                    options={
                        "temperature": 0.3,
                        "num_predict": 300,
                        "num_gpu": 99,
                        "num_ctx": 2048,
                    },
                )
                
                msg = response["message"]
                messages.append(msg)
                
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    answer = (msg.get("content") or "").strip()
                    return self.clean_response(answer) if answer else "Не поняла вопрос."
                
                for call in tool_calls:
                    name = call["function"]["name"]
                    args = call["function"].get("arguments", {}) or {}
                    fn = self.kids_tools_by_name.get(name)
                    
                    if fn is None:
                        result = f"Инструмент {name} недоступен."
                    else:
                        try:
                            result = fn(**args) if args else fn()
                        except Exception as e:
                            result = f"Ошибка инструмента: {e}"
                    
                    messages.append({"role": "tool", "content": str(result)})
            
            return "Не смогла обработать запрос."
            
        except Exception as e:
            log.error(f"KidsLLM критическая ошибка: {e}")
            import traceback
            traceback.print_exc()
            return f"Произошла ошибка: {e}"
