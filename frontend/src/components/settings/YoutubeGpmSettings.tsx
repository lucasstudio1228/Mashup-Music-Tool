import { useEffect, useState } from "react";
import {
  useGpmConfig, useUpdateGpmConfig, useGpmProfiles,
  useChannelMappings, useCreateMapping, useUpdateMapping, useDeleteMapping,
  type ChannelMapping, type MappingBody,
} from "../../api/youtube";

const inputCls =
  "w-full bg-[#0f1117] border border-white/10 rounded-lg px-2.5 py-1.5 text-sm " +
  "text-white placeholder:text-gray-600 focus:outline-none focus:border-violet-500";

const emptyMapping: MappingBody = {
  instrument: "",
  music_style: "",
  gpm_profile_id: "",
  channel_name: "",
  default_hashtags: "",
  language: "en-US",
};

function ProfileSelect({
  value,
  profiles,
  onChange,
}: {
  value: string;
  profiles: { id: string; name: string; group_name?: string }[];
  onChange: (v: string) => void;
}) {
  // Nếu profile đã chọn không còn trong danh sách (API tắt), vẫn hiển thị id.
  const known = profiles.some((p) => p.id === value);
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputCls}
    >
      <option value="">— chọn profile —</option>
      {!known && value && <option value={value}>{value} (không thấy)</option>}
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.group_name ? `[${p.group_name}] ` : ""}
          {p.name}
        </option>
      ))}
    </select>
  );
}

export function YoutubeGpmSettings() {
  const { data: config } = useGpmConfig();
  const updateConfig = useUpdateGpmConfig();
  const { data: profilesResult, refetch: refetchProfiles, isFetching } =
    useGpmProfiles();
  const { data: mappings } = useChannelMappings();
  const createMapping = useCreateMapping();
  const updateMapping = useUpdateMapping();
  const deleteMapping = useDeleteMapping();

  const [baseUrl, setBaseUrl] = useState("");
  const [exePath, setExePath] = useState("");
  const [cfgDirty, setCfgDirty] = useState(false);

  useEffect(() => {
    if (config) {
      setBaseUrl(config.gpm_api_base_url);
      setExePath(config.gpm_exe_path ?? "");
      setCfgDirty(false);
    }
  }, [config]);

  const profiles = profilesResult?.profiles ?? [];

  // Form thêm mới.
  const [newRow, setNewRow] = useState<MappingBody>(emptyMapping);
  // Bản nháp khi sửa từng dòng (id → body).
  const [edits, setEdits] = useState<Record<number, MappingBody>>({});

  function rowBody(m: ChannelMapping): MappingBody {
    return (
      edits[m.id] ?? {
        instrument: m.instrument,
        music_style: m.music_style,
        gpm_profile_id: m.gpm_profile_id,
        channel_name: m.channel_name,
        default_hashtags: m.default_hashtags,
        language: m.language,
      }
    );
  }
  function setRow(id: number, patch: Partial<MappingBody>) {
    setEdits((prev) => ({
      ...prev,
      [id]: { ...rowBody(mappings!.find((x) => x.id === id)!), ...prev[id], ...patch },
    }));
  }

  return (
    <div className="space-y-4 border-t border-white/10 pt-5">
      <h3 className="text-base font-semibold text-white">
        📺 YouTube / GPMLogin
      </h3>
      <p className="text-xs text-amber-300/80">
        ⓘ Phần này lưu bằng các nút riêng bên dưới (💾 Lưu cấu hình GPM · 💾 Lưu từng
        dòng ánh xạ · ➕ Thêm) — <b>không</b> dùng nút "Lưu API (OpenAI)" ở cuối.
      </p>

      {/* GPM config */}
      <div className="space-y-2 rounded-lg border border-white/5 bg-[#0f1117] p-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">
            GPMLogin Local API base URL
          </label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => { setBaseUrl(e.target.value); setCfgDirty(true); }}
            placeholder="http://localhost:19996"
            className={inputCls}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">
            Đường dẫn GPMLogin.exe (tuỳ chọn — để tự mở app nếu chưa chạy)
          </label>
          <input
            type="text"
            value={exePath}
            onChange={(e) => { setExePath(e.target.value); setCfgDirty(true); }}
            placeholder="C:\\...\\GPMLogin.exe"
            className={inputCls}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() =>
              updateConfig.mutate(
                { gpm_api_base_url: baseUrl.trim(), gpm_exe_path: exePath.trim() },
                { onSuccess: () => { setCfgDirty(false); refetchProfiles(); } }
              )
            }
            disabled={updateConfig.isPending || !cfgDirty}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            💾 Lưu cấu hình GPM
          </button>
          <button
            onClick={() => refetchProfiles()}
            disabled={isFetching}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            {isFetching ? "⏳ Đang kiểm tra…" : "🔌 Test kết nối / tải profiles"}
          </button>
        </div>
        {profilesResult && (
          <p className={`text-xs ${profilesResult.ok ? "text-green-400" : "text-yellow-400"}`}>
            {profilesResult.ok
              ? `✅ Thấy ${profiles.length} profile (nguồn: ${profilesResult.base_url}).`
              : `⚠️ Không gọi được API (${profilesResult.error ?? "lỗi"}). Đã thử đọc từ database.db.`}
          </p>
        )}
        <p className="text-xs text-gray-600">
          Bật <b>Local API</b> trong GPMLogin để tool mở profile + lấy CDP. Khi API tắt,
          danh sách profile vẫn đọc được từ database.db (đủ để cấu hình ánh xạ).
        </p>
      </div>

      {/* Mapping table */}
      <div>
        <div className="mb-2 text-sm font-medium text-gray-300">
          Bảng ánh xạ kênh — (nhạc cụ + phong cách) → profile GPMLogin
        </div>

        <div className="space-y-2">
          {(mappings ?? []).map((m) => {
            const body = rowBody(m);
            const dirty = JSON.stringify(body) !== JSON.stringify({
              instrument: m.instrument, music_style: m.music_style,
              gpm_profile_id: m.gpm_profile_id, channel_name: m.channel_name,
              default_hashtags: m.default_hashtags, language: m.language,
            });
            return (
              <div key={m.id} className="rounded-lg border border-white/10 bg-[#0f1117] p-2.5">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <input className={inputCls} placeholder="nhạc cụ" value={body.instrument}
                         onChange={(e) => setRow(m.id, { instrument: e.target.value })} />
                  <input className={inputCls} placeholder="phong cách" value={body.music_style}
                         onChange={(e) => setRow(m.id, { music_style: e.target.value })} />
                  <input className={inputCls} placeholder="ngôn ngữ (en-US)" value={body.language}
                         onChange={(e) => setRow(m.id, { language: e.target.value })} />
                  <div className="col-span-2 sm:col-span-3">
                    <ProfileSelect value={body.gpm_profile_id} profiles={profiles}
                                   onChange={(v) => setRow(m.id, { gpm_profile_id: v })} />
                  </div>
                  <input className={inputCls} placeholder="tên kênh" value={body.channel_name}
                         onChange={(e) => setRow(m.id, { channel_name: e.target.value })} />
                  <input className={`${inputCls} col-span-1 sm:col-span-2`} placeholder="#hashtag mặc định"
                         value={body.default_hashtags}
                         onChange={(e) => setRow(m.id, { default_hashtags: e.target.value })} />
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={() => updateMapping.mutate({ id: m.id, body },
                      { onSuccess: () => setEdits((p) => { const n = { ...p }; delete n[m.id]; return n; }) })}
                    disabled={!dirty || updateMapping.isPending}
                    className="rounded-lg border border-white/15 px-2.5 py-1 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
                  >
                    💾 Lưu
                  </button>
                  <button
                    onClick={() => { if (confirm("Xoá dòng ánh xạ này?")) deleteMapping.mutate(m.id); }}
                    disabled={deleteMapping.isPending}
                    className="rounded-lg border border-red-500/30 px-2.5 py-1 text-xs font-medium text-red-400 hover:bg-red-950/30 disabled:opacity-40"
                  >
                    🗑 Xoá
                  </button>
                  {dirty && <span className="text-xs text-yellow-300">chưa lưu</span>}
                </div>
              </div>
            );
          })}
          {(mappings ?? []).length === 0 && (
            <p className="text-xs text-gray-500">Chưa có ánh xạ nào.</p>
          )}
        </div>

        {/* Add new */}
        <div className="mt-3 rounded-lg border border-dashed border-white/15 bg-[#0f1117] p-2.5">
          <div className="mb-1.5 text-xs font-semibold text-gray-400">➕ Thêm ánh xạ mới</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <input className={inputCls} placeholder="nhạc cụ" value={newRow.instrument}
                   onChange={(e) => setNewRow({ ...newRow, instrument: e.target.value })} />
            <input className={inputCls} placeholder="phong cách" value={newRow.music_style}
                   onChange={(e) => setNewRow({ ...newRow, music_style: e.target.value })} />
            <input className={inputCls} placeholder="ngôn ngữ (en-US)" value={newRow.language}
                   onChange={(e) => setNewRow({ ...newRow, language: e.target.value })} />
            <div className="col-span-2 sm:col-span-3">
              <ProfileSelect value={newRow.gpm_profile_id} profiles={profiles}
                             onChange={(v) => setNewRow({ ...newRow, gpm_profile_id: v })} />
            </div>
            <input className={inputCls} placeholder="tên kênh" value={newRow.channel_name}
                   onChange={(e) => setNewRow({ ...newRow, channel_name: e.target.value })} />
            <input className={`${inputCls} col-span-1 sm:col-span-2`} placeholder="#hashtag mặc định"
                   value={newRow.default_hashtags}
                   onChange={(e) => setNewRow({ ...newRow, default_hashtags: e.target.value })} />
          </div>
          <button
            onClick={() => createMapping.mutate(
              { ...newRow, language: newRow.language.trim() || "en-US" },
              { onSuccess: () => setNewRow(emptyMapping) })}
            disabled={
              createMapping.isPending ||
              !newRow.instrument.trim() || !newRow.music_style.trim() || !newRow.gpm_profile_id
            }
            className="mt-2 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40"
          >
            ➕ Thêm
          </button>
        </div>
      </div>
    </div>
  );
}
