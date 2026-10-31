import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '../store/auth'
import type { ChatMessage, Meeting, Notice, Participant, SharedFile, User, WhiteboardState } from '../types'
const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api').replace(/\/$/, '')
export const api = axios.create({ baseURL: API_URL, timeout: 30_000 })
type StoredSession = {access:string|null;refresh:string|null;user:User|null}
type RetryableRequest = InternalAxiosRequestConfig & {_retry?:boolean}
const sessionStorageKey='connectx-session'
const sessionChannel=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('connectx-session')
let refreshTask: Promise<string>|null = null

function readStoredSession():StoredSession|null{
  try{
    const parsed=JSON.parse(localStorage.getItem(sessionStorageKey)||'null') as {state?:StoredSession}|null
    return parsed?.state||null
  }catch{return null}
}

sessionChannel?.addEventListener('message',(event:MessageEvent<{type?:string;userId?:number;access?:string;refresh?:string}>)=>{
  const message=event.data
  const current=useAuthStore.getState()
  const user=current.user
  if(message.type==='session-refreshed'&&message.access&&message.refresh&&user&&user.id===message.userId){
    current.setSession(message.access,message.refresh,user)
  }else if(message.type==='session-ended'&&current.user?.id===message.userId){
    current.clear()
  }
})
if(import.meta.hot)import.meta.hot.dispose(()=>sessionChannel?.close())

function refreshAccessToken(failedAccess?:string,force=false):Promise<string>{
  if(refreshTask)return refreshTask
  const starting=useAuthStore.getState()
  if(!starting.refresh||!starting.user)return Promise.reject(new Error('Your session has expired. Sign in again.'))
  const startingAccess=starting.access
  const startingRefresh=starting.refresh
  const userId=starting.user.id

  const refresh=async()=>{
    const current=useAuthStore.getState()
    if(current.user?.id!==userId||!current.refresh)throw new Error('Your session changed. Sign in again.')

    const stored=readStoredSession()
    if(stored?.access&&stored.refresh&&stored.user?.id===userId){
      const changedElsewhere=stored.access!==startingAccess||stored.refresh!==startingRefresh
      if(changedElsewhere){
        current.setSession(stored.access,stored.refresh,current.user)
        return stored.access
      }
      if(!force&&failedAccess&&stored.access!==failedAccess)return stored.access
    }

    const refreshToken=current.refresh
    try{
      const {data}=await axios.post(`${API_URL}/auth/refresh/`,{refresh:refreshToken},{timeout:15_000})
      const latest=useAuthStore.getState()
      if(latest.user?.id!==userId)throw new Error('Your session changed. Sign in again.')
      if(latest.refresh!==refreshToken&&latest.access)return latest.access
      latest.setSession(data.access,data.refresh||refreshToken,latest.user)
      sessionChannel?.postMessage({type:'session-refreshed',userId,access:data.access,refresh:data.refresh||refreshToken})
      return data.access as string
    }catch(error){
      const latest=useAuthStore.getState()
      if(axios.isAxiosError(error)&&error.response?.status===401&&latest.user?.id===userId&&latest.refresh===refreshToken){
        latest.clear()
        sessionChannel?.postMessage({type:'session-ended',userId})
      }
      throw error
    }
  }

  const coordinated=()=>typeof navigator!=='undefined'&&navigator.locks?.request
    ?navigator.locks.request('connectx-session-refresh',refresh)
    :refresh()
  refreshTask=coordinated().finally(()=>{refreshTask=null})
  return refreshTask
}

api.interceptors.request.use((config) => { const token=useAuthStore.getState().access; if(token) config.headers.Authorization=`Bearer ${token}`; return config })
api.interceptors.response.use((response)=>response, async (error:AxiosError) => {
  const original = error.config as RetryableRequest
  const noRefresh=['/auth/login/','/auth/register/','/auth/forgot-password/','/auth/reset-password/','/auth/refresh/']
  if(error.response?.status===401 && original && !original._retry && !noRefresh.some(path=>original.url?.includes(path))) {
    const refresh=useAuthStore.getState().refresh
    if(!refresh) { const userId=useAuthStore.getState().user?.id;useAuthStore.getState().clear();if(userId)sessionChannel?.postMessage({type:'session-ended',userId});throw error }
    original._retry=true
    try {
      const header=String(original.headers?.Authorization||'')
      const failedAccess=header.replace(/^Bearer\s+/i,'')||undefined
      const access=await refreshAccessToken(failedAccess)
      original.headers.Authorization=`Bearer ${access}`
      return api(original)
    } catch { throw error }
  }
  throw error
})
export const authApi = {
  async login(username:string,password:string){const {data}=await api.post('/auth/login/',{username,password});return data as {access:string;refresh:string;user:User}},
  async register(form:{name:string;username:string;email:string;password:string}){const {data}=await api.post('/auth/register/',form);return data as {access:string;refresh:string;user:User}},
  async me(){const {data}=await api.get('/auth/me/');return data as User},
  async refreshSession(){return refreshAccessToken(undefined,true)},
  async logout(){const current=useAuthStore.getState();const refresh=current.refresh;const userId=current.user?.id;try{if(refresh)await api.post('/auth/logout/',{refresh})}finally{current.clear();if(userId)sessionChannel?.postMessage({type:'session-ended',userId})}},
}
export const meetingApi = {
  async list(params?:Record<string,string>){const {data}=await api.get('/meetings/',{params});return data as Meeting[]},
  async create(body:Record<string,unknown>){const {data}=await api.post('/meetings/',body);return data as Meeting},
  async get(id:string){const {data}=await api.get(`/meetings/${id}/`);return data as Meeting},
  async update(id:string,body:Record<string,unknown>){const {data}=await api.patch(`/meetings/${id}/`,body);return data as Meeting},
  async translationToken(id:string){const {data}=await api.post(`/meetings/${id}/translation-token/`);return data as {authorization_token:string;region:string}},
  async lookup(code:string){const {data}=await api.get(`/meetings/lookup/${encodeURIComponent(code)}/`);return data as Meeting},
  async join(idOrCode:string,password?:string){const {data}=await api.post(`/meetings/${encodeURIComponent(idOrCode)}/join/`,{password});return data as {waiting:boolean;meeting:Meeting;participant?:Participant}},
  async joinStatus(id:string){const {data}=await api.get(`/meetings/${id}/join/status/`);return data as {state:string;meeting:Meeting}},
  async leave(id:string){await api.post(`/meetings/${id}/leave/`)},
  async end(id:string){await api.post(`/meetings/${id}/end/`)},
  async token(id:string){const {data}=await api.post(`/meetings/${id}/token/`);return data as {url:string;token:string;room:string}},
  async participants(id:string){const {data}=await api.get(`/meetings/${id}/participants/`);return data as Participant[]},
  async moderate(id:string,participant_id:number,action:string){const {data}=await api.post(`/meetings/${id}/participants/`,{participant_id,action});return data as Participant},
  async invite(id:string,emails:string[]){const {data}=await api.post(`/meetings/${id}/invite/`,{emails});return data as {link:string;detail:string;invited:number}},
  async cancel(id:string){await api.delete(`/meetings/${id}/`)},
}
export const collabApi={
  async messages(id:string){const {data}=await api.get(`/meetings/${id}/messages/`);return data as ChatMessage[]},
  async files(id:string){const {data}=await api.get(`/meetings/${id}/files/`);return data as SharedFile[]},
  async upload(id:string,file:File,onProgress?:(p:number)=>void){const fd=new FormData();fd.append('file',file);const {data}=await api.post(`/meetings/${id}/files/`,fd,{onUploadProgress:(e)=>{if(e.total)onProgress?.(Math.round(e.loaded/e.total*100))}});return data as SharedFile},
  async previewFile(id:number){const {data}=await api.get(`/files/${id}/`,{responseType:'blob'});return data as Blob},
  async download(id:number,name:string){const {data}=await api.get(`/files/${id}/`,{responseType:'blob'});const url=URL.createObjectURL(data);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000)},
  async deleteFile(id:number){await api.delete(`/files/${id}/`)},
  async board(id:string){const {data}=await api.get(`/meetings/${id}/whiteboard/`);return data.state as WhiteboardState},
  async saveBoard(id:string,state:WhiteboardState){await api.post(`/meetings/${id}/whiteboard/`,{state})},
}
export const userApi={
  async stats(){const {data}=await api.get('/stats/');return data as {hosted_meetings:number;completed_meetings:number;upcoming_meetings:number;minutes_hosted:number}},
  async notifications(){const {data}=await api.get('/notifications/');return data as {unread:number;results:Notice[]}},
  async markRead(id?:number){await api.patch(id?`/notifications/${id}/read/`:'/notifications/read-all/')},
  async updateProfile(body:FormData|Record<string,unknown>){const {data}=await api.patch('/auth/me/',body);return data as User},
  async changePassword(old_password:string,new_password:string){await api.post('/auth/change-password/',{old_password,new_password})},
  async forgotPassword(email:string){return api.post('/auth/forgot-password/',{email})},
  async resetPassword(uid:string,token:string,password:string){return api.post('/auth/reset-password/',{uid,token,password})},
}
export function meetingSocket(id:string,onEvent:(event:Record<string,unknown>)=>void){
  const access=useAuthStore.getState().access
  if(!access) throw new Error('Sign in to connect to this meeting.')
  const base=(import.meta.env.VITE_WS_URL || API_URL.replace(/^http/,'ws').replace(/\/api$/,'')).replace(/\/$/,'')
  const socket=new WebSocket(`${base}/ws/meetings/${id}/`,['connectx-auth',access])
  socket.onmessage=(event)=>{try{onEvent(JSON.parse(event.data) as Record<string,unknown>)}catch{/* Ignore malformed events. */}}
  return socket
}

