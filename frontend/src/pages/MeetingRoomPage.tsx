import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { GridLayout, LiveKitRoom, ParticipantTile, RoomAudioRenderer, TrackLoop, useLocalParticipant, useParticipants, useRoomContext, useTracks } from '@livekit/components-react'
import { ConnectionState, RoomEvent, Track } from 'livekit-client'
import { ArrowLeft, Camera, CameraOff, Captions, ChevronDown, Copy, FileText, Languages, Mic, MicOff, MonitorUp, MoreHorizontal, Phone, PhoneOff, Settings2, ShieldCheck, Users, X } from 'lucide-react'
import { authApi, meetingApi, meetingSocket } from '../services/api'
import { useAuthStore } from '../store/auth'
import type { ChatMessage, Meeting } from '../types'
import { Avatar, Button } from '../components/ui'
import { MeetingTranslationPanel, type TranslationState } from '../components/MeetingTranslationPanel'
import { errorMessage, errorName, errorStatus } from '../services/errors'
import { formatDate, toast } from '../utils/ui'
import { ChatPanel, FilesPanel, ParticipantsPanel, Whiteboard } from '../components/MeetingPanels'
import { createSpeechOutput, startSpeechTranslation, translationLanguages, type SpeechOutput, type TranslationCaption, type TranslationSession } from '../services/meetingTranslation'

type Devices={cameraOn?:boolean;micOn?:boolean;camera?:string;mic?:string;speaker?:string}
export function MeetingRoomPage(){const{id=''}=useParams();const nav=useNavigate();const location=useLocation();const deviceState=(location.state||{}) as Devices;const meeting=useQuery({queryKey:['meeting',id],queryFn:()=>meetingApi.get(id),retry:false});const token=useQuery({queryKey:['livekit-token',id],queryFn:()=>meetingApi.token(id),enabled:!!meeting.data,retry:false});const exit=()=>nav('/app',{replace:true});if(meeting.isLoading||token.isLoading)return <div className="room-loading"><span className="spinner"/><b>Connecting to your meeting</b><span>Setting up a secure room…</span></div>;if(meeting.isError||!meeting.data)return <RoomProblem title="Meeting unavailable" detail="This meeting is no longer available to you." back={exit}/>;if(token.isError||!token.data)return <RoomProblem title="Video service is not ready" detail={errorMessage(token.error,'Connect the LiveKit service in your project environment, then try again.') } back={exit}/>;return <LiveKitRoom serverUrl={token.data.url} token={token.data.token} connect audio={deviceState.micOn===false?false:{deviceId:deviceState.mic||undefined}} video={deviceState.cameraOn===false?false:{deviceId:deviceState.camera||undefined}} onError={(e)=>toast(e.message||'LiveKit could not connect.','error')} onMediaDeviceFailure={(_,kind)=>toast(`${kind||'Media device'} could not be started. Check browser permissions.`,'error')}><MeetingRoom meeting={meeting.data} devices={deviceState} exit={exit}/></LiveKitRoom>}

function RoomProblem({title,detail,back}:{title:string;detail:string;back:()=>void}){return <div className="room-problem"><span className="room-problem-icon"><ShieldCheck/></span><span className="eyebrow">CONNECTX MEETINGS</span><h1>{title}</h1><p>{detail}</p><Button onClick={back} icon={<ArrowLeft size={16}/>}>Return to workspace</Button></div>}

function MeetingRoom({meeting,devices,exit}:{meeting:Meeting;devices:Devices;exit:()=>void}){
  const nav=useNavigate()
  const qc=useQueryClient()
  const me=useAuthStore(s=>s.user)
  const room=useRoomContext()
  const {localParticipant,isMicrophoneEnabled,isCameraEnabled,isScreenShareEnabled}=useLocalParticipant()
  const participants=useParticipants()
  const tracks=useTracks([{source:Track.Source.Camera,withPlaceholder:true},{source:Track.Source.ScreenShare,withPlaceholder:false}],{onlySubscribed:false})
  const audioTracks=useTracks([{source:Track.Source.Microphone,withPlaceholder:false}],{onlySubscribed:false})
  const [panel,setPanel]=useState<'chat'|'people'|'files'|'translation'|null>(null)
  const panelRef=useRef<'chat'|'people'|'files'|'translation'|null>(null)
  const [unread,setUnread]=useState(0)
  const [board,setBoard]=useState(false)
  const [invite,setInvite]=useState(false)
  const [socket,setSocket]=useState<WebSocket|null>(null)
  const [sound,setSound]=useState(devices.speaker||'')
  const [deviceList,setDeviceList]=useState<MediaDeviceInfo[]>([])
  const [showDeviceMenu,setShowDeviceMenu]=useState(false)
  const [showMore,setShowMore]=useState(false)
  const [connecting,setConnecting]=useState(true)
  const [mediaConnection,setMediaConnection]=useState<ConnectionState>(room.state)
  const [translationLanguage,setTranslationLanguage]=useState(()=>localStorage.getItem(`connectx-translation-language-${me?.id||'guest'}`)||'en')
  const [translationAudio,setTranslationAudio]=useState(false)
  const [translationCaptions,setTranslationCaptions]=useState(true)
  const [caption,setCaption]=useState<TranslationCaption|null>(null)
  const [translationState,setTranslationState]=useState<TranslationState>(meeting.translation_enabled?'starting':'idle')
  const [translationError,setTranslationError]=useState('')
  const translationPrefs=useRef({language:translationLanguage,audio:translationAudio,captions:translationCaptions})
  const translationSession=useRef<TranslationSession|null>(null)
  const speechOutput=useRef<SpeechOutput|null>(null)
  const pendingSpeech=useRef<string[]>([])
  const captionTimer=useRef<number|undefined>(undefined)
  const ownRole=meeting.host.id===me?.id?'host':'participant'
  const isHost=ownRole==='host'
  const hostAudioTrack=audioTracks.find(track=>track.participant.identity===String(meeting.host.id))?.publication?.track
  useEffect(()=>{panelRef.current=panel;if(panel==='chat')setUnread(0)},[panel])
  useEffect(()=>{translationPrefs.current={language:translationLanguage,audio:translationAudio,captions:translationCaptions};localStorage.setItem(`connectx-translation-language-${me?.id||'guest'}`,translationLanguage)},[me?.id,translationLanguage,translationAudio,translationCaptions])
  useEffect(()=>{
    let alive=true
    let timer:number|undefined
    let current:WebSocket|undefined
    let attempts=0
    const schedule=(delay?:number)=>{
      if(!alive)return
      setConnecting(true)
      const wait=delay??Math.min(1000*2**Math.min(attempts++,5),30_000)
      timer=window.setTimeout(connect,wait)
    }
    const connect=()=>{
      if(!alive)return
      try{
        const socket=meetingSocket(meeting.id,(event)=>{
          const kind=event.event
          if(kind==='chat_message'&&event.message){
            const incoming=event.message as ChatMessage
            qc.setQueryData<ChatMessage[]>(['messages',meeting.id],old=>[...(old||[]),incoming])
            if(panelRef.current!=='chat'&&incoming.sender.id!==me?.id)setUnread(value=>value+1)
          }
          if(kind==='translation_settings'&&typeof event.translation_enabled==='boolean'&&typeof event.translation_source_language==='string'){
            qc.setQueryData<Meeting>(['meeting',meeting.id],old=>old?{...old,translation_enabled:event.translation_enabled as boolean,translation_source_language:event.translation_source_language as string}:old)
            setTranslationState(event.translation_enabled?'starting':'idle')
            if(!event.translation_enabled){setTranslationAudio(false);setCaption(null);setTranslationError('')}
          }
          if(kind==='translation_status'&&['starting','live','error','stopped'].includes(String(event.status))){
            setTranslationState(event.status as TranslationState)
            setTranslationError(typeof event.detail==='string'?event.detail:'')
          }
          if(kind==='translation_caption'){
            const data=event as unknown as TranslationCaption
            const target=translationPrefs.current.language
            if(data.translations?.[target]){
              setCaption(data)
              if(captionTimer.current)window.clearTimeout(captionTimer.current)
              captionTimer.current=window.setTimeout(()=>setCaption(null),data.final?6500:4500)
              const source=translationLanguages.find(language=>language.source===data.source_language)?.code
              if(data.final&&translationPrefs.current.audio&&target!==source){const text=data.translations[target];if(speechOutput.current)speechOutput.current.speak(text);else pendingSpeech.current=[...pendingSpeech.current,text].slice(-5)}
            }
          }
          if(['participant_joined','participant_left','participant_online','participant_offline','participant_approved','participant_removed','participant_muted','participant_status'].includes(String(kind)))void qc.invalidateQueries({queryKey:['participants',meeting.id]})
          if(kind==='meeting_ended'||(kind==='participant_removed'&&event.user_id===me?.id)){
            toast(kind==='meeting_ended'?'The host ended this meeting.':'You were removed from this meeting.','info')
            void room.disconnect()
            nav('/app',{replace:true})
          }
        })
        current=socket
        socket.onopen=()=>{if(alive&&current===socket){attempts=0;setSocket(socket);setConnecting(false)}}
        socket.onclose=(event)=>{
          if(!alive||current!==socket)return
          current=undefined
          setSocket(null)
          if(event.code===4403){setConnecting(false);toast('You no longer have access to this meeting.','error');return}
          if(event.code===4401){
            setConnecting(true)
            timer=window.setTimeout(()=>{
              void authApi.refreshSession().then(()=>schedule(250)).catch(error=>{
                if(errorStatus(error)===401){toast('Your session expired. Sign in again.','error');nav('/login',{replace:true});return}
                schedule()
              })
            },250)
            return
          }
          schedule()
        }
      }catch(error){
        if(errorName(error)==='Error'&&errorMessage(error,'')==='Sign in to connect to this meeting.'){
          setConnecting(false)
          return
        }
        schedule()
      }
    }
    connect()
    return()=>{alive=false;if(timer)window.clearTimeout(timer);current?.close();setSocket(null)}
  },[meeting.id,me?.id,nav,qc,room])
  const localMicrophoneTrack=localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack
  useEffect(()=>{
    if(!isHost||!meeting.translation_enabled){setTranslationState('idle');setTranslationError('');return}
    if(!isMicrophoneEnabled||!localMicrophoneTrack){const detail='Turn on your microphone to start speech translation.';setTranslationState('error');setTranslationError(detail);if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'translation_status',status:'error',source_language:meeting.translation_source_language,detail}));return}
    if(!socket||socket.readyState!==WebSocket.OPEN){setTranslationState('starting');return}

    let active=true
    let session:TranslationSession|undefined
    let tokenRefresh:number|undefined
    const sendStatus=(status:TranslationState| 'stopped',detail='')=>{
      if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'translation_status',status,source_language:meeting.translation_source_language,detail}))
    }
    const start=async()=>{
      setTranslationState('starting');setTranslationError('');sendStatus('starting')
      try{
        const credentials=await meetingApi.translationToken(meeting.id)
        const started=await startSpeechTranslation({
          track:localMicrophoneTrack,
          sourceLocale:meeting.translation_source_language,
          credentials,
          onCaption:caption=>{if(active&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'translation_caption',...caption}))},
          onError:detail=>{if(!active)return;setTranslationState('error');setTranslationError(detail);sendStatus('error',detail)},
        })
        if(!active){await started.stop();return}
        session=started;translationSession.current=started;setTranslationState('live');sendStatus('live')
        tokenRefresh=window.setInterval(()=>{
          void meetingApi.translationToken(meeting.id).then(next=>{if(active)session?.setAuthorizationToken(next.authorization_token)}).catch(()=>{})
        },8*60*1000)
      }catch(error){
        if(!active)return
        const detail=errorMessage(error,'Speech translation could not start. Check the Azure Speech settings and microphone.')
        setTranslationState('error');setTranslationError(detail);sendStatus('error',detail)
      }
    }
    void start()
    return()=>{
      active=false
      if(tokenRefresh)window.clearInterval(tokenRefresh)
      if(translationSession.current===session)translationSession.current=null
      if(session)void session.stop()
    }
  },[isHost,isMicrophoneEnabled,localMicrophoneTrack,meeting.id,meeting.translation_enabled,meeting.translation_source_language,socket])
  useEffect(()=>{
    if(!translationAudio||!meeting.translation_enabled){pendingSpeech.current=[];speechOutput.current?.stop();speechOutput.current=null;return}
    let active=true
    let output:SpeechOutput|undefined
    let tokenRefresh:number|undefined
    const start=async()=>{
      try{
        const credentials=await meetingApi.translationToken(meeting.id)
        output=await createSpeechOutput(credentials,translationLanguage)
        if(!active){output.stop();return}
        speechOutput.current=output
        pendingSpeech.current.splice(0).forEach(text=>output?.speak(text))
        tokenRefresh=window.setInterval(()=>{
          void meetingApi.translationToken(meeting.id).then(next=>{if(active)output?.setAuthorizationToken(next.authorization_token)}).catch(()=>{})
        },8*60*1000)
      }catch(error){
        if(!active)return
        const detail=errorMessage(error,'Translated voice could not start. Check the Azure Speech settings.')
        setTranslationAudio(false);setTranslationError(detail);toast(detail,'error')
      }
    }
    void start()
    return()=>{
      active=false
      if(tokenRefresh)window.clearInterval(tokenRefresh)
      pendingSpeech.current=[]
      if(speechOutput.current===output)speechOutput.current=null
      output?.stop()
    }
  },[meeting.id,meeting.translation_enabled,translationAudio,translationLanguage])
  useEffect(()=>{
    if(isHost)return
    const remote=hostAudioTrack as import('livekit-client').RemoteAudioTrack|undefined
    if(!remote)return
    remote.setVolume(translationAudio&&meeting.translation_enabled?0:1)
    return()=>remote.setVolume(1)
  },[hostAudioTrack,isHost,meeting.translation_enabled,translationAudio])
  const saveTranslationSettings=async(body:Record<string,unknown>)=>{
    try{
      const updated=await meetingApi.update(meeting.id,body)
      qc.setQueryData<Meeting>(['meeting',meeting.id],updated)
      setTranslationError('')
      toast(body.translation_enabled===undefined?'Speaker language updated':updated.translation_enabled?'Live translation enabled':'Live translation disabled')
      if(body.translation_enabled===false){setTranslationAudio(false);setCaption(null)}
    }catch(error){
      const detail=errorMessage(error,'Translation settings could not be saved.')
      setTranslationError(detail);setTranslationState('error');toast(detail,'error')
    }
  }
  useEffect(()=>{void navigator.mediaDevices?.enumerateDevices().then(setDeviceList).catch(()=>{})},[isCameraEnabled,isMicrophoneEnabled])
  useEffect(()=>{const onConnected=()=>{setConnecting(false);toast('You’re connected.','success')};room.on(RoomEvent.Connected,onConnected);return()=>{room.off(RoomEvent.Connected,onConnected)}},[room])
  useEffect(()=>{const update=(state:ConnectionState)=>setMediaConnection(state);update(room.state);room.on(RoomEvent.ConnectionStateChanged,update);return()=>{room.off(RoomEvent.ConnectionStateChanged,update)}},[room])
  useEffect(()=>{if(sound)void room.switchActiveDevice('audiooutput',sound).catch(()=>toast('Could not switch speakers on this browser.','error'))},[room,sound])
  const close=async()=>{try{await meetingApi.leave(meeting.id)}catch{/* The meeting may have ended already. */}await room.disconnect();exit()}
  const end=async()=>{if(!window.confirm('End this meeting for everyone?'))return;try{await meetingApi.end(meeting.id);await room.disconnect();nav('/app',{replace:true});toast('Meeting ended for everyone')}catch(error){toast(errorMessage(error,'Could not end the meeting.'),'error')}}
  const toggleShare=async()=>{try{await localParticipant.setScreenShareEnabled(!isScreenShareEnabled,{audio:false,selfBrowserSurface:'include',surfaceSwitching:'include'});if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'participant_status',status:{is_sharing_screen:!isScreenShareEnabled}}))}catch(error){if(errorName(error)!=='NotAllowedError')toast(errorMessage(error,'Screen sharing is unavailable.'),'error')}}
  const toggleCamera=async()=>{try{await localParticipant.setCameraEnabled(!isCameraEnabled,devices.camera?{deviceId:{exact:devices.camera}}:undefined);if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'participant_status',status:{is_camera_off:isCameraEnabled}}))}catch{toast('Camera could not be started. Check browser permissions.','error')}}
  const toggleMic=async()=>{try{await localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled,devices.mic?{deviceId:{exact:devices.mic}}:undefined);if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'participant_status',status:{is_muted:isMicrophoneEnabled}}))}catch{toast('Microphone could not be started. Check browser permissions.','error')}}
  const screen=participants.find(p=>p.isScreenShareEnabled)
  const mediaLabel=mediaConnection===ConnectionState.Connected?'Connected':mediaConnection===ConnectionState.Reconnecting||mediaConnection===ConnectionState.SignalReconnecting?'Reconnecting':mediaConnection===ConnectionState.Connecting?'Connecting':'Disconnected'
  return <div className="meeting-room"><header className="room-header"><div className="room-header-title"><Link to="/app" className="room-back" onClick={e=>{e.preventDefault();void close()}}><ArrowLeft size={17}/></Link><span><b>{meeting.title}</b><small>Meeting ID · {meeting.code}</small></span></div><div className="room-header-center"><span className="room-live-dot"/> LIVE <span className={`room-connection-state room-connection-${mediaConnection}`}><i/>{mediaLabel}</span><span className="room-header-divider"/>{formatDate(meeting.started_at||meeting.scheduled_at,{hour:'numeric',minute:'2-digit'})}</div><div className="room-header-actions"><button className="room-copy" onClick={async()=>{try{await navigator.clipboard.writeText(`${location.origin}/join/${meeting.code}`);toast('Meeting link copied')}catch{toast(`${location.origin}/join/${meeting.code}`,'info')}}}><Copy size={15}/> Copy invite</button><span className="room-secure"><ShieldCheck size={15}/> Encrypted</span><Avatar user={me} size="sm"/></div></header><div className="room-body"><main className={`room-stage-area ${panel?'with-panel':''}`}><div className="stage-info"><div><span className="stage-title">{screen?`${screen.name} is sharing their screen`:'Your meeting room'}</span><span className="stage-subtitle"><Users size={14}/> {participants.length} participant{participants.length===1?'':'s'}{mediaConnection!==ConnectionState.Connected?' · '+mediaLabel+' call':connecting?' · Reconnecting live updates':''}</span></div><div className="stage-indicators">{isScreenShareEnabled&&<span className="sharing-indicator"><MonitorUp size={14}/> You’re sharing</span>}<button className={`stage-action ${panel==='people'?'stage-action-active':''}`} onClick={()=>setPanel(panel==='people'?null:'people')} title="Participants"><Users size={17}/></button><button className="stage-action" onClick={()=>setInvite(true)} title="Invite people"><MoreHorizontal size={18}/></button></div></div><div className={`video-canvas ${tracks.length===1?'single-tile':''}`}><RoomAudioRenderer/><GridLayout tracks={tracks} className="participant-grid"><TrackLoop tracks={tracks}><ParticipantTile className="participant-tile"/></TrackLoop></GridLayout>{translationCaptions&&meeting.translation_enabled&&caption&&caption.translations[translationLanguage]&&<div className="translation-subtitle" aria-live="polite"><span><Captions size={13}/> {translationLanguages.find(language=>language.source===caption.source_language)?.label.split(' · ')[0]||'Translated speech'}</span><b dir={translationLanguage==='ur'?'rtl':'auto'}>{caption.translations[translationLanguage]}</b></div>}{tracks.length===0&&<div className="room-empty-video"><div className="room-empty-orbit orbit-a"/><div className="room-empty-orbit orbit-b"/><div className="room-empty-center"><Avatar user={me} size="lg"/></div><span>{participants.length<=1?'It’s just you here':'Waiting for video'}</span><small>{participants.length<=1?'Invite someone to join the conversation':'Participants can turn on their cameras any time'}</small></div>}</div><footer className="room-controls"><div className="room-control-group"><div className="control-with-menu"><button className={`control-btn ${isMicrophoneEnabled?'':'control-off'}`} onClick={()=>void toggleMic()} title={isMicrophoneEnabled?'Mute microphone':'Unmute microphone'}>{isMicrophoneEnabled?<Mic/>:<MicOff/>}<span>{isMicrophoneEnabled?'Mute':'Unmute'}</span></button><button className="control-chevron" onClick={()=>{setShowDeviceMenu(!showDeviceMenu);setShowMore(false)}}><ChevronDown size={14}/></button>{showDeviceMenu&&<div className="device-popover"><b>Audio & video</b><label>Microphone<select value={devices.mic||''} onChange={e=>void localParticipant.setMicrophoneEnabled(true,{deviceId:{exact:e.target.value}})}>{deviceList.filter(d=>d.kind==='audioinput').map((d,i)=><option key={d.deviceId} value={d.deviceId}>{d.label||`Microphone ${i+1}`}</option>)}</select></label><label>Camera<select value={devices.camera||''} onChange={e=>void localParticipant.setCameraEnabled(true,{deviceId:{exact:e.target.value}})}>{deviceList.filter(d=>d.kind==='videoinput').map((d,i)=><option key={d.deviceId} value={d.deviceId}>{d.label||`Camera ${i+1}`}</option>)}</select></label><label>Speaker<select value={sound} onChange={e=>setSound(e.target.value)}>{deviceList.filter(d=>d.kind==='audiooutput').map((d,i)=><option key={d.deviceId} value={d.deviceId}>{d.label||`Speaker ${i+1}`}</option>)}</select></label></div>}</div><button className={`control-btn ${isCameraEnabled?'':'control-off'}`} onClick={()=>void toggleCamera()} title={isCameraEnabled?'Turn off camera':'Turn on camera'}>{isCameraEnabled?<Camera/>:<CameraOff/>}<span>{isCameraEnabled?'Camera':'Camera off'}</span></button><button className={`control-btn ${isScreenShareEnabled?'control-sharing':''}`} onClick={()=>void toggleShare()} title={isScreenShareEnabled?'Stop sharing':'Share screen'}>{isScreenShareEnabled?<MonitorUp/>:<MonitorUp/>}<span>{isScreenShareEnabled?'Stop share':'Share'}</span></button><button className={`control-btn ${panel==='translation'?'control-selected':''}`} onClick={()=>setPanel(panel==='translation'?null:'translation')} title="Live meeting translation"><Languages/><span>Translate</span></button><button className={`control-btn ${panel==='chat'?'control-selected':''}`} onClick={()=>setPanel(panel==='chat'?null:'chat')} title="Meeting chat"><MessageIcon/><span>Chat</span>{unread>0&&<b className="room-unread">{unread>99?'99+':unread}</b>}</button><button className={`control-btn ${panel==='people'?'control-selected':''}`} onClick={()=>setPanel(panel==='people'?null:'people')} title="Participants"><Users/><span>People</span><b className="control-count">{participants.length}</b></button><button className="control-btn" onClick={()=>setBoard(true)} title="Open collaborative whiteboard"><BoardIcon/><span>Whiteboard</span></button><button className={`control-btn ${panel==='files'?'control-selected':''}`} onClick={()=>setPanel(panel==='files'?null:'files')} title="Shared files"><FileText/><span>Files</span></button><div className="control-with-menu"><button className="control-btn" onClick={()=>{setShowMore(!showMore);setShowDeviceMenu(false)}} title="More meeting actions"><Settings2/><span>More</span></button>{showMore&&<div className="device-popover more-popover"><b>Meeting details</b><span>Secure room · {meeting.code}</span><button onClick={()=>{setInvite(true);setShowMore(false)}}><Users size={15}/> Invite participants</button><button onClick={()=>{setPanel('files');setShowMore(false)}}><FileText size={15}/> Open shared files</button></div>}</div></div><div className="room-control-end">{isHost&&<button className="end-meeting-btn" onClick={()=>void end()}><PhoneOff size={17}/><span>End for all</span></button>}<button className="leave-meeting-btn" onClick={()=>void close()}><Phone size={17}/><span>Leave</span></button></div></footer></main>{panel&&<aside className="room-panel"><div className="room-panel-head"><div><span className="eyebrow">{panel==='chat'?'LIVE CONVERSATION':panel==='people'?'THE ROOM':panel==='translation'?'LANGUAGE ACCESS':'MEETING RESOURCES'}</span><h2>{panel==='chat'?'In-meeting chat':panel==='people'?'Participants':panel==='translation'?'Live translation':'Shared files'}</h2></div><button className="icon-btn" onClick={()=>setPanel(null)}><X size={18}/></button></div>{panel==='chat'?<ChatPanel meetingId={meeting.id} socket={socket}/>:panel==='people'?<ParticipantsPanel meeting={meeting} socket={socket}/>:panel==='translation'?<MeetingTranslationPanel isHost={isHost} enabled={meeting.translation_enabled} sourceLanguage={meeting.translation_source_language} targetLanguage={translationLanguage} captionsOn={translationCaptions} audioOn={translationAudio} state={translationState} error={translationError} onEnabledChange={enabled=>void saveTranslationSettings({translation_enabled:enabled})} onSourceChange={language=>void saveTranslationSettings({translation_source_language:language})} onTargetChange={language=>setTranslationLanguage(language)} onCaptionsChange={setTranslationCaptions} onAudioChange={enabled=>{setTranslationError('');setTranslationAudio(enabled)}}/>:<FilesPanel meeting={meeting} socket={socket}/>}</aside>}</div>{board&&<Whiteboard meeting={meeting} socket={socket} onClose={()=>setBoard(false)}/ >}{invite&&<InviteModal meeting={meeting} onClose={()=>setInvite(false)}/>}</div>
}

function MessageIcon(){return <span className="message-bubble-icon"><i/></span>}
function BoardIcon(){return <span className="board-control-icon"><i/><i/><i/></span>}

function InviteModal({meeting,onClose}:{meeting:Meeting;onClose:()=>void}){const[email,setEmail]=useState('');const[working,setWorking]=useState(false);const[link,setLink]=useState(meeting.external_url||meeting.link||`${window.location.origin}/join/${meeting.code}`);const copy=async()=>{try{await navigator.clipboard.writeText(link);toast('Invitation link copied')}catch{toast(link,'info')}};const invite=async(e:React.FormEvent)=>{e.preventDefault();const emails=email.split(/[,;\n]+/).map(value=>value.trim()).filter(Boolean);setWorking(true);try{const result=await meetingApi.invite(meeting.id,emails);setLink(result.link);toast(result.detail);setEmail('')}catch(error){toast(errorMessage(error,'Invitation could not be sent.'),'error')}finally{setWorking(false)}};return <div className="modal-backdrop" onClick={onClose}><div className="modal-card invite-card" onClick={e=>e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">GROW THE ROOM</span><h2>Invite people</h2></div><button className="icon-btn" onClick={onClose}><X/></button></div><p className="invite-description">Bring people into <b>{meeting.title}</b>. Separate email addresses with commas.</p><form className="invite-form" onSubmit={invite}><div className="input-wrap"><MailIcon/><input type="text" value={email} onChange={e=>setEmail(e.target.value)} placeholder="Enter one or more email addresses" required/></div><Button type="submit" disabled={working}>{working?'Sending…':'Send invitation'}</Button></form><div className="invite-divider"><span/> OR SHARE A LINK <span/></div><div className="invite-link"><span>{link}</span><Button variant="secondary" size="sm" onClick={copy} icon={<Copy size={15}/>}>Copy</Button></div><div className="invite-security"><ShieldCheck size={16}/> Only people with the meeting link and password can join.</div></div></div>}
function MailIcon(){return <span className="mail-icon">✉</span>}

