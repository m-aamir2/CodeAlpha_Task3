import { Captions, Languages, LoaderCircle, Volume2, VolumeX } from 'lucide-react'
import { languageForTarget, translationLanguages } from '../services/meetingTranslation'

export type TranslationState = 'idle' | 'starting' | 'live' | 'error'

export function MeetingTranslationPanel({
  isHost,
  enabled,
  sourceLanguage,
  targetLanguage,
  captionsOn,
  audioOn,
  state,
  error,
  onEnabledChange,
  onSourceChange,
  onTargetChange,
  onCaptionsChange,
  onAudioChange,
}: {
  isHost: boolean
  enabled: boolean
  sourceLanguage: string
  targetLanguage: string
  captionsOn: boolean
  audioOn: boolean
  state: TranslationState
  error: string
  onEnabledChange: (enabled: boolean) => void
  onSourceChange: (language: string) => void
  onTargetChange: (language: string) => void
  onCaptionsChange: (enabled: boolean) => void
  onAudioChange: (enabled: boolean) => void
}) {
  const target = languageForTarget(targetLanguage)
  return <div className="translation-panel">
    <div className="translation-panel-intro"><span className="translation-panel-icon"><Languages size={18}/></span><div><b>Live translation</b><small>Listen in your language while the original audio stays available to everyone else.</small></div></div>
    {isHost&&<section className="translation-admin-settings">
      <label>Speaker language<select value={sourceLanguage} onChange={event=>onSourceChange(event.target.value)}>
        {translationLanguages.map(language=><option key={language.source} value={language.source}>{language.label}</option>)}
      </select></label>
      <div className="translation-setting-row"><div><b>Enable for this meeting</b><small>Translate the host’s microphone for participants.</small></div><button className={`translation-toggle ${enabled?'translation-toggle-on':''}`} role="switch" aria-checked={enabled} aria-label="Enable live translation for this meeting" onClick={()=>onEnabledChange(!enabled)}><i/></button></div>
    </section>}
    {!isHost&&!enabled&&<div className="translation-not-ready"><b>Waiting for the host</b><span>The host can enable live translation and choose the spoken language.</span></div>}
    <section className="translation-listener-settings">
      <label>Listen in<select value={targetLanguage} onChange={event=>onTargetChange(event.target.value)}>
        {translationLanguages.map(language=><option key={language.code} value={language.code}>{language.label}</option>)}
      </select></label>
      <div className="translation-setting-row"><div><b><Captions size={15}/> Show translated subtitles</b><small>Subtitles appear over the meeting video.</small></div><button className={`translation-toggle ${captionsOn?'translation-toggle-on':''}`} role="switch" aria-checked={captionsOn} aria-label="Show translated subtitles" onClick={()=>onCaptionsChange(!captionsOn)}><i/></button></div>
      <button className={`translation-audio-button ${audioOn?'translation-audio-on':''}`} disabled={!enabled||state==='starting'} onClick={()=>onAudioChange(!audioOn)}>{state==='starting'?<LoaderCircle size={16} className="translation-spin"/>:audioOn?<Volume2 size={16}/>:<VolumeX size={16}/>}<span>{audioOn?'Translated audio on':'Play translated audio'}</span></button>
    </section>
    <div className={`translation-status translation-status-${state}`} role="status" aria-live="polite"><i/>{state==='live'?'Speech translation is live':state==='starting'?'Connecting speech translation…':state==='error'?'Translation needs attention':enabled?'Ready when the host starts translation':'Translation is off'}</div>
    {error&&<p className="translation-error">{error}</p>}
    <p className="translation-privacy">Azure Speech processes the host’s live audio to create translations. Original LiveKit audio is unchanged for other participants.</p>
    {audioOn&&<p className="translation-language-note">Translated voice: {target.label}</p>}
  </div>
}
