import os
import re
import json
import logging
from typing import Any
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)

class LLMError(RuntimeError):
    pass

class LLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        # 1. Carga de API Key
        self.api_key = (api_key or os.getenv("GOOGLE_API_KEY") or "").strip()
        if not self.api_key:
            raise LLMError("Falta GOOGLE_API_KEY en .env")

        # 2. Configuración
        genai.configure(api_key=self.api_key)  # Configuración corregida (sin transport="rest")
        self.model_name = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")

        raw_fallback = os.getenv("GEMINI_FALLBACK_MODELS", "gemini-1.5-flash,gemini-2.0-flash")
        self.fallback_models = [m.strip() for m in raw_fallback.split(",") if m.strip()]
        self.timeout = float(os.getenv("GEMINI_TIMEOUT", "45"))

        # Pre-calculamos la lista de intentos una sola vez
        self.intentos = [self.model_name]
        for m in self.fallback_models:
            if m != self.model_name and m not in self.intentos:
                self.intentos.append(m)

    def _build_history(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        chat_history: list[dict[str, Any]] = []

        for msg in messages:
            role = (msg.get("role") or "user").strip().lower()
            content = msg.get("content")
            images = msg.get("images") or []  # <--- NUEVO: Extraemos las imágenes

            # Preparar las partes del mensaje (texto + imágenes)
            parts = []
            if content:
                parts.append(str(content).strip())

            # Agregamos las imágenes a las partes del mensaje
            for img in images:
                parts.append(img)

            # Si no hay texto ni imágenes, saltamos
            if not parts:
                continue

            if role == "system":
                # System prompt normalmente solo lleva texto
                if content:
                    system_parts.append(str(content).strip())
            elif role in ("assistant", "model"):
                chat_history.append({"role": "model", "parts": parts})
            else:
                chat_history.append({"role": "user", "parts": parts})

        # Quitamos la validación estricta de "Si no hay chat_history" porque 
        # a veces el primer mensaje puede ser solo una foto.
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, chat_history

    # AHORA ES UNA FUNCIÓN ASÍNCRONA
    async def chat(self, messages: list[dict[str, Any]], temperature: float = 0.65, max_tokens: int = 900) -> str:
        if not messages:
            raise LLMError("No se recibieron mensajes para el modelo.")

        system_instruction, chat_history = self._build_history(messages)

        last_error = None

        # Usamos self.intentos en lugar de recalcularlo
        for model_name in self.intentos:
            try:
                logging.info(f"Intentando modelo: {model_name}")
                
                # 1. Determinar si el modelo soporta system_instruction (familia Gemini)
                is_gemini = "gemini" in model_name.lower()
                
                # 2. Preparar los argumentos base del modelo
                model_kwargs = {
                    "model_name": model_name,
                    "generation_config": {
                        "temperature": max(0.0, min(2.0, float(temperature))),
                        "max_output_tokens": max(1, int(max_tokens)),
                    }
                }
                
                # 3. Clonar el historial para no modificar el original si hay reintentos (fallbacks)
                current_history = [{"role": m["role"], "parts": list(m["parts"])} for m in chat_history]
                
                # 4. Manejar la instrucción del sistema de forma dinámica
                if is_gemini:
                    if system_instruction:
                        model_kwargs["system_instruction"] = system_instruction
                else:
                    # Para Gemma u otros: inyectamos el contexto en el primer mensaje del usuario
                    if system_instruction and current_history:
                        for msg in current_history:
                            if msg["role"] == "user":
                                # Prependemos el prompt del sistema al mensaje del usuario
                                msg["parts"][0] = f"[Contexto del Sistema]\n{system_instruction}\n\n[Mensaje del Usuario]\n{msg['parts'][0]}"
                                break

                # 5. Instanciar el modelo desempaquetando los argumentos
                model = genai.GenerativeModel(**model_kwargs)

                response = await model.generate_content_async(
                    current_history,  # Pasamos el historial adaptado
                    request_options={"timeout": self.timeout},
                )

                try:
                    text_value = (response.text or "").strip()
                except ValueError as ve:
                    last_error = f"ValueError en {model_name}: {ve}"
                    logging.warning(last_error)
                    continue

                if text_value:
                    if model_name != self.intentos[0]:
                        logging.warning(f"Fallback activado: modelo {model_name} usado en vez de {self.intentos[0]}")
                    return text_value

                last_error = f"Respuesta vacía en {model_name}"
                logging.warning(last_error)
                continue

            except google_exceptions.ResourceExhausted:
                last_error = f"Cuota agotada en {model_name}"
                logging.warning(last_error)
                continue
            except google_exceptions.InvalidArgument as e:
                last_error = f"Modelo {model_name} no encontrado o inválido: {e}"
                logging.warning(last_error)
                continue
            except Exception as e:
                last_error = f"Error en {model_name}: {e}"
                logging.error(last_error)
                continue

        logging.error(f"No se pudo generar respuesta. Último error: {last_error}")
        raise LLMError(f"No se pudo generar respuesta. Último error: {last_error}")

    _PARAM_DEFAULTS = {"sarcasmo": 5, "empatia": 5, "hostilidad": 5, "humor": 5, "jerga": 5, "concision": 5}

    async def generar_parametros_persona(self, descripcion_texto: str) -> dict:
        """Convierte texto en 6 parámetros numéricos (1-10) de forma robusta."""
        logging.info(f"generar_parametros_persona llamado con texto de {len(descripcion_texto)} chars")
        prompt = (
            "Eres un clasificador de personalidades para un bot de Discord.\n"
            "Analiza la descripción y califica CADA rasgo del 1 al 10.\n"
            "USA EL RANGO COMPLETO. NO pongas todo en 5.\n\n"
            f"DESCRIPCIÓN:\n{descripcion_texto}\n\n"
            "RASGOS A EVALUAR:\n"
            "- sarcasmo: 1=Siempre literal y sincero, 10=Ironía brutal constante\n"
            "- empatia: 1=Frío e indiferente, 10=Extremadamente comprensivo\n"
            "- hostilidad: 1=Pacífico y amable, 10=Insulta y ataca constantemente\n"
            "- humor: 1=Serio y formal, 10=Todo es chiste\n"
            "- jerga: 1=Formal y educado, 10=Puro slang callejero\n"
            "- concision: 1=Mensajes muy largos, 10=Ultra breve y cortante\n\n"
            "Responde SOLO con JSON. Ejemplo: {\"sarcasmo\":7,\"empatia\":2,\"hostilidad\":8,\"humor\":6,\"jerga\":9,\"concision\":8}"
        )

        # Intentar con cada modelo disponible (igual que chat)
        last_error = None
        for model_name in self.intentos:
            # Intento 1: con response_mime_type (fuerza JSON)
            # Intento 2: sin response_mime_type (por si el modelo no lo soporta)
            configs = [
                {"temperature": 0.1, "response_mime_type": "application/json"},
                {"temperature": 0.1},
            ]
            for config in configs:
                try:
                    logging.info(f"Calibrando parámetros con modelo={model_name}, config={config}")
                    model = genai.GenerativeModel(
                        model_name=model_name,
                        generation_config=config,
                    )
                    response = await model.generate_content_async(
                        [{"role": "user", "parts": [prompt]}],
                        request_options={"timeout": self.timeout},
                    )
                    raw = (response.text or "").strip()
                    logging.info(f"Respuesta raw de calibración: {raw[:300]}")

                    # Extraer JSON del texto (por si viene envuelto en markdown)
                    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
                    if not json_match:
                        last_error = f"No se encontró JSON en respuesta de {model_name}: {raw[:100]}"
                        logging.warning(last_error)
                        continue

                    data = json.loads(json_match.group(0))
                    data_clean = {str(k).lower().strip(): v for k, v in data.items()}

                    result = {}
                    all_default = True
                    for key in self._PARAM_DEFAULTS:
                        val = data_clean.get(key, 5)
                        try:
                            clamped = max(1, min(10, int(val)))
                        except (ValueError, TypeError):
                            clamped = 5
                        result[key] = clamped
                        if clamped != 5:
                            all_default = False

                    if all_default:
                        logging.warning(f"Modelo {model_name} devolvió todo en 5, reintentando...")
                        last_error = f"Todos los valores en 5 con {model_name}"
                        continue

                    logging.info(f"Parámetros calibrados exitosamente: {result}")
                    return result

                except json.JSONDecodeError as e:
                    last_error = f"JSON inválido de {model_name}: {e}"
                    logging.warning(last_error)
                    continue
                except google_exceptions.InvalidArgument as e:
                    last_error = f"Argumento inválido en {model_name}: {e}"
                    logging.warning(last_error)
                    continue
                except google_exceptions.ResourceExhausted:
                    last_error = f"Cuota agotada en {model_name}"
                    logging.warning(last_error)
                    break  # Siguiente modelo
                except Exception as e:
                    last_error = f"Error calibrando con {model_name}: {e}"
                    logging.error(last_error)
                    continue

        logging.error(f"Calibración falló en todos los modelos. Último error: {last_error}")
        return dict(self._PARAM_DEFAULTS)