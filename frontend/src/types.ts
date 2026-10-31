export interface User { id: number; username: string; name: string; email: string; avatar: string; bio: string; date_joined?: string }
export interface Meeting { id: string; code: string; host: User; title: string; description: string; external_url: string|null; status: 'scheduled'|'live'|'ended'|'cancelled'; scheduled_at: string|null; started_at: string|null; ended_at: string|null; duration_seconds: number; waiting_room: boolean; has_password: boolean; participant_count: number; link: string; created_at: string }
export interface Participant { id: number; user: User; role: 'host'|'cohost'|'participant'; state: 'waiting'|'active'|'left'|'removed'; joined_at: string|null; left_at?: string|null; is_muted: boolean; is_camera_off: boolean; is_sharing_screen: boolean }
export interface ChatMessage { id: number; sender: User; text: string; created_at: string }
export interface SharedFile { id: number; original_name: string; size: number; content_type: string; uploader: User; created_at: string }
export interface Notice { id: number; kind: string; title: string; body: string; meeting: string|null; meeting_code: string; read_at: string|null; created_at: string }
export interface WhiteboardState { strokes: Array<{ points: Array<{x:number;y:number}>; color: string; size: number; erase?: boolean; text?: string }> }

