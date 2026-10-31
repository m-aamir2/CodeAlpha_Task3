import type * as SpeechSdkTypes from 'microsoft-cognitiveservices-speech-sdk'

export const translationLanguages = [
  { code: 'en', label: 'English', locale: 'en-US', voice: 'en-US-JennyNeural', source: 'en-US' },
  { code: 'ur', label: 'اردو · Urdu', locale: 'ur-PK', voice: 'ur-PK-UzmaNeural', source: 'ur-PK' },
  { code: 'zh-Hans', label: '简体中文 · Chinese', locale: 'zh-CN', voice: 'zh-CN-XiaoxiaoNeural', source: 'zh-CN' },
  { code: 'ar', label: 'العربية · Arabic', locale: 'ar-SA', voice: 'ar-SA-ZariyahNeural', source: 'ar-SA' },
  { code: 'hi', label: 'हिन्दी · Hindi', locale: 'hi-IN', voice: 'hi-IN-SwaraNeural', source: 'hi-IN' },
  { code: 'fr', label: 'Français · French', locale: 'fr-FR', voice: 'fr-FR-DeniseNeural', source: 'fr-FR' },
  { code: 'es', label: 'Español · Spanish', locale: 'es-ES', voice: 'es-ES-ElviraNeural', source: 'es-ES' },
  { code: 'de', label: 'Deutsch · German', locale: 'de-DE', voice: 'de-DE-KatjaNeural', source: 'de-DE' },
  { code: 'ja', label: '日本語 · Japanese', locale: 'ja-JP', voice: 'ja-JP-NanamiNeural', source: 'ja-JP' },
  { code: 'pt', label: 'Português · Portuguese', locale: 'pt-BR', voice: 'pt-BR-FranciscaNeural', source: 'pt-BR' },
] as const

export type TranslationLanguage = typeof translationLanguages[number]
export type TranslationCaption = {
  caption_id: string
  source_language: string
  source_text: string
  translations: Record<string, string>
  final: boolean
  created_at: string
}
export type SpeechCredentials = { authorization_token: string; region: string }
export type TranslationSession = { setAuthorizationToken(token: string): void; stop(): Promise<void> }
export type SpeechOutput = { setAuthorizationToken(token: string): void; speak(text: string): void; stop(): void }

export function languageForTarget(code: string) {
  return translationLanguages.find(language => language.code === code) ?? translationLanguages[0]
}

export async function startSpeechTranslation({
  track,
  sourceLocale,
  credentials,
  onCaption,
  onError,
}: {
  track: MediaStreamTrack
  sourceLocale: string
  credentials: SpeechCredentials
  onCaption: (caption: Omit<TranslationCaption, 'created_at'>) => void
  onError: (message: string) => void
}): Promise<TranslationSession> {
  const sdk = await import('microsoft-cognitiveservices-speech-sdk')
  const config = sdk.SpeechTranslationConfig.fromAuthorizationToken(credentials.authorization_token, credentials.region)
  config.speechRecognitionLanguage = sourceLocale
  translationLanguages.forEach(language => config.addTargetLanguage(language.code))
  const stream = new MediaStream([track])
  const audio = sdk.AudioConfig.fromStreamInput(stream)
  const recognizer = new sdk.TranslationRecognizer(config, audio)
  let stopped = false
  const emit = (event: SpeechSdkTypes.TranslationRecognitionEventArgs, final: boolean) => {
    const result = event.result
    if (!result.text?.trim()) return
    const translations: Record<string, string> = {}
    translationLanguages.forEach(language => {
      const translated = result.translations.get(language.code)
      if (translated?.trim()) translations[language.code] = translated.trim()
    })
    if (!Object.keys(translations).length) return
    onCaption({
      caption_id: result.resultId || `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      source_language: sourceLocale,
      source_text: result.text.trim(),
      translations,
      final,
    })
  }
  recognizer.recognizing = (_sender, event) => emit(event, false)
  recognizer.recognized = (_sender, event) => {
    if (event.result.reason === sdk.ResultReason.TranslatedSpeech) emit(event, true)
  }
  recognizer.canceled = (_sender, event) => {
    if (!stopped && event.reason === sdk.CancellationReason.Error) {
      onError(event.errorDetails || 'Speech recognition disconnected. Check the Azure Speech region and network.')
    }
  }

  await new Promise<void>((resolve, reject) => {
    recognizer.startContinuousRecognitionAsync(resolve, reject)
  })

  return {
    setAuthorizationToken(token) { recognizer.authorizationToken = token },
    stop() {
      stopped = true
      return new Promise(resolve => {
        recognizer.stopContinuousRecognitionAsync(() => {
          recognizer.close()
          audio.close()
          resolve()
        }, () => {
          recognizer.close()
          audio.close()
          resolve()
        })
      })
    },
  }
}

export async function createSpeechOutput(credentials: SpeechCredentials, languageCode: string): Promise<SpeechOutput> {
  const sdk = await import('microsoft-cognitiveservices-speech-sdk')
  const language = languageForTarget(languageCode)
  const config = sdk.SpeechConfig.fromAuthorizationToken(credentials.authorization_token, credentials.region)
  config.speechSynthesisVoiceName = language.voice
  const synthesizer = new sdk.SpeechSynthesizer(config)
  let queue = Promise.resolve()
  let stopped = false
  return {
    setAuthorizationToken(token) { synthesizer.authorizationToken = token },
    speak(text) {
      const clean = text.trim()
      if (!clean || stopped) return
      queue = queue.then(() => new Promise<void>(resolve => {
        if (stopped) return resolve()
        synthesizer.speakTextAsync(clean, () => resolve(), () => resolve())
      }))
    },
    stop() {
      stopped = true
      synthesizer.close()
    },
  }
}
