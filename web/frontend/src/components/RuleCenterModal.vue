<template>
  <Teleport to="body">
    <div class="rc-overlay" @click.self="emit('close')">
      <section class="rc-modal" role="dialog" aria-modal="true" aria-label="YARA-X 规则中心">
        <header class="rc-header">
          <div>
            <span class="rc-kicker">YARA-X 1.19</span>
            <h1>规则中心</h1>
            <p>安全导入、编辑、验证并版本化管理内存扫描规则</p>
          </div>
          <button class="rc-close" title="关闭" @click="emit('close')"><AppIcon name="x" /></button>
        </header>

        <nav class="rc-tabs">
          <button
            v-for="item in tabs"
            :key="item.id"
            :class="{ active: tab === item.id }"
            @click="selectTab(item.id)"
          >{{ item.label }}<span v-if="item.id === 'installed'">{{ packages.length }}</span></button>
        </nav>

        <main class="rc-body">
          <div v-if="busy" class="rc-loading">正在处理…</div>
          <div v-if="error" class="rc-alert error">{{ error }}</div>

          <section v-if="tab === 'installed'" class="rc-section">
            <div class="rc-toolbar">
              <div>
                <h2>已安装规则包</h2>
                <p>只有通过整包编译并启用的版本会显示在扫描插件列表。</p>
              </div>
              <button class="rc-primary" @click="createPackage">新建本地包</button>
            </div>
            <div v-if="!packages.length" class="rc-empty">尚无规则包，创建一个包或从 ZIP / 市场安装。</div>
            <div class="rc-package-grid">
              <article v-for="pkg in packages" :key="pkg.id" class="rc-card">
                <div class="rc-card-head">
                  <div>
                    <h3>{{ pkg.name }}</h3>
                    <code>package.{{ pkg.id }}</code>
                  </div>
                  <span class="rc-status" :class="pkg.status">{{ statusLabel(pkg.status) }}</span>
                </div>
                <p>{{ pkg.description || '暂无描述' }}</p>
                <div class="rc-card-meta">
                  <span>v{{ pkg.version || '草稿' }}</span>
                  <span>{{ pkg.source_type }}</span>
                  <span v-if="pkg.update_available" class="update">待更新</span>
                </div>
                <div class="rc-card-actions">
                  <label class="rc-switch">
                    <input
                      type="checkbox"
                      :checked="pkg.enabled"
                      :disabled="!pkg.active_version_id"
                      @change="togglePackage(pkg, $event.target.checked)"
                    /><span></span>{{ pkg.enabled ? '已启用' : '已禁用' }}
                  </label>
                  <button @click="openEditor(pkg)">编辑</button>
                  <button @click="openHistory(pkg)">历史</button>
                  <a
                    v-if="pkg.active_version_id"
                    :href="exportYaraPackageUrl(pkg.id)"
                    target="_blank"
                  >导出</a>
                  <button class="danger" @click="removePackage(pkg)">删除</button>
                </div>
              </article>
            </div>
          </section>

          <section v-else-if="tab === 'market'" class="rc-section">
            <div class="rc-toolbar">
              <div><h2>在线市场</h2><p>仅连接受约束的 GitHub 仓库与 codeload 下载域名。</p></div>
              <button @click="showCustomSource = !showCustomSource">添加 GitHub 源</button>
            </div>
            <form v-if="showCustomSource" class="rc-inline-form" @submit.prevent="addCustomSource">
              <input v-model="customSource.repository" required placeholder="owner/repo" />
              <input v-model="customSource.ref" placeholder="ref（默认 main）" />
              <input v-model="customSource.subdirectory" placeholder="子目录（可选）" />
              <button class="rc-primary">添加</button>
            </form>
            <div class="rc-market-list">
              <article v-for="source in market" :key="source.id" class="rc-market-item">
                <div>
                  <h3>{{ source.name }} <span v-if="source.builtin">精选</span></h3>
                  <code>{{ source.repository }}@{{ source.ref }}<template v-if="source.subdirectory">/{{ source.subdirectory }}</template></code>
                  <p>{{ source.license }}</p>
                </div>
                <button
                  class="rc-primary"
                  :disabled="source.installed"
                  @click="installSource(source)"
                >{{ source.installed ? '已安装' : '下载、校验并安装' }}</button>
              </article>
            </div>
          </section>

          <section v-else-if="tab === 'import'" class="rc-section rc-import">
            <div class="rc-dropzone" :class="{ ready: zipFile }">
              <AppIcon name="package" :size="30" />
              <h2>{{ zipFile ? zipFile.name : '导入规则 ZIP' }}</h2>
              <p>最大 50 MiB；解压后 200 MiB / 5000 文件 / 100:1 压缩率。</p>
              <label class="rc-primary">选择 ZIP<input type="file" accept=".zip" hidden @change="chooseZip" /></label>
              <button v-if="zipFile" :disabled="busy" @click="previewZip">预览并校验</button>
            </div>
            <div v-if="zipPreview" class="rc-preview">
              <div class="rc-preview-title">
                <h3>{{ zipPreview.manifest?.name || '未命名规则包' }}</h3>
                <span :class="zipPreview.valid ? 'valid' : 'invalid'">
                  {{ zipPreview.valid ? '编译兼容' : '需要修复' }}
                </span>
              </div>
              <p>{{ zipPreview.files?.length || 0 }} 个文件 · 入口：
                {{ (zipPreview.entrypoints || []).join(', ') || '未推断' }}</p>
              <label class="rc-entrypoints">
                入口文件（每行一个）
                <textarea v-model="entrypointText" rows="4"></textarea>
              </label>
              <DiagnosticsList :items="zipPreview.diagnostics || []" />
              <div class="rc-actions">
                <button class="rc-primary" @click="confirmImport(false)" :disabled="!zipPreview.valid">安装并启用</button>
                <button @click="confirmImport(true)">保存为禁用草稿</button>
              </div>
            </div>
          </section>

          <section v-else-if="tab === 'editor'" class="rc-editor-section">
            <div v-if="!editorPackage" class="rc-empty">
              请先从“已安装”选择一个规则包进行编辑。
            </div>
            <template v-else>
              <aside class="rc-file-panel">
                <div class="rc-file-head">
                  <div><strong>{{ editorPackage.name }}</strong><small>修订 {{ draft?.revision || '-' }}</small></div>
                  <button title="新建文件" @click="newFile"><AppIcon name="plus" /></button>
                </div>
                <input
                  v-model="fileSearch"
                  class="rc-file-search"
                  placeholder="文件名 / 回车全文搜索…"
                  @keydown.enter="searchContent"
                />
                <button
                  v-for="hit in searchHits"
                  :key="`${hit.path}:${hit.line}`"
                  class="rc-search-hit"
                  @click="openFile(hit.path)"
                ><span>{{ hit.path }}:{{ hit.line }}</span><small>{{ hit.preview }}</small></button>
                <button
                  v-for="file in filteredDraftFiles"
                  :key="file.path"
                  class="rc-file"
                  :class="{ active: activeFile === file.path }"
                  @click="openFile(file.path)"
                >
                  <span>{{ file.path }}</span><small>{{ formatSize(file.size) }}</small>
                </button>
              </aside>
              <div class="rc-workbench">
                <div class="rc-editor-toolbar">
                  <div class="rc-open-tabs">
                    <button
                      v-for="path in openFiles"
                      :key="path"
                      :class="{ active: activeFile === path }"
                      @click="openFile(path)"
                    >{{ path }}<i v-if="dirtyFiles.has(path)">●</i><b @click.stop="closeFile(path)">×</b></button>
                  </div>
                  <div class="rc-editor-actions">
                    <button @click="renameFile" :disabled="!activeFile">重命名</button>
                    <button @click="deleteFile" :disabled="!activeFile || activeFile === 'zero-yara.json'">删除</button>
                    <button @click="saveFile" :disabled="!activeFile || !dirtyFiles.has(activeFile)">保存 <kbd>⌘S</kbd></button>
                    <button @click="validateDraft">校验</button>
                    <button class="rc-primary" @click="commitDraft">提交并激活</button>
                  </div>
                </div>
                <div v-if="activeFile" ref="editorHost" class="rc-editor"></div>
                <div v-else class="rc-empty">从左侧选择文件。</div>
                <DiagnosticsList
                  :items="diagnostics"
                  class="rc-diagnostics"
                  @jump="jumpDiagnostic"
                />
              </div>
            </template>
          </section>

          <section v-else-if="tab === 'history'" class="rc-section">
            <div class="rc-toolbar">
              <div><h2>版本历史</h2><p>{{ historyPackage?.name || '请选择规则包' }} · 恢复会创建一个新版本，不覆盖历史。</p></div>
            </div>
            <div v-if="!versions.length" class="rc-empty">暂无已提交版本。</div>
            <article v-for="version in versions" :key="version.id" class="rc-version">
              <div>
                <strong>v{{ version.version }}</strong>
                <span>#{{ version.sequence }}</span>
                <code>{{ version.content_digest.slice(0, 12) }}</code>
                <small>{{ new Date(version.created_at * 1000).toLocaleString() }}</small>
              </div>
              <div class="rc-version-actions">
                <button @click="showVersionDiff(version)">查看差异</button>
                <button @click="restoreVersion(version)">恢复为新版本</button>
              </div>
            </article>
            <pre v-if="versionDiff" class="rc-version-diff">{{ versionDiff }}</pre>
          </section>
        </main>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { HighlightStyle, StreamLanguage, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import AppIcon from './AppIcon.vue'
import DiagnosticsList from './YaraDiagnosticsList.vue'
import {
  addYaraMarketSource, commitYaraPackage, confirmYaraZip, createYaraPackage,
  deleteYaraDraftFile, deleteYaraPackage, ensureYaraDraft, exportYaraPackageUrl,
  forkYaraPackage, getYaraDraftFile, getYaraVersionDiff, getYaraVersions,
  installYaraMarketSource, listYaraMarket,
  listYaraPackages, previewYaraZip, renameYaraDraftFile, restoreYaraVersion,
  saveYaraDraftFile, searchYaraDraft, setYaraPackageEnabled, validateYaraPackage,
} from '../api'

const emit = defineEmits(['close', 'packages-changed'])
const tabs = [
  { id: 'installed', label: '已安装' }, { id: 'market', label: '在线市场' },
  { id: 'import', label: 'ZIP 导入' }, { id: 'editor', label: '规则编辑器' },
  { id: 'history', label: '版本历史' },
]
const tab = ref('installed')
const busy = ref(false)
const error = ref('')
const packages = ref([])
const market = ref([])
const showCustomSource = ref(false)
const customSource = reactive({ repository: '', ref: 'main', subdirectory: '' })
const zipFile = ref(null)
const zipPreview = ref(null)
const entrypointText = ref('')
const editorPackage = ref(null)
const historyPackage = ref(null)
const draft = ref(null)
const versions = ref([])
const versionDiff = ref('')
const openFiles = ref([])
const activeFile = ref('')
const buffers = reactive({})
const fileShas = reactive({})
const dirtyFiles = reactive(new Set())
const diagnostics = ref([])
const editorHost = ref(null)
const fileSearch = ref('')
const searchHits = ref([])
let editorView = null
let autosaveTimer = null
const filteredDraftFiles = computed(() => {
  const needle = fileSearch.value.trim().toLowerCase()
  const files = draft.value?.files || []
  return needle ? files.filter(file => file.path.toLowerCase().includes(needle)) : files
})

const yaraLanguage = StreamLanguage.define({
  token(stream) {
    if (stream.match(/\/\/.*/)) return 'comment'
    if (stream.match(/\/\*/)) { while (!stream.eol() && !stream.match(/\*\//, false)) stream.next(); stream.match(/\*\//); return 'comment' }
    if (stream.match(/"(?:[^"\\]|\\.)*"/)) return 'string'
    if (stream.match(/\/(?:[^/\\]|\\.)+\/[a-z]*/)) return 'regexp'
    if (stream.match(/\b(?:rule|private|global|import|include|meta|strings|condition)\b/)) return 'keyword'
    if (stream.match(/\b(?:and|or|not|any|all|none|of|them|for|in|at|true|false|filesize|entrypoint)\b/)) return 'bool'
    if (stream.match(/\$[A-Za-z0-9_*]+/)) return 'variableName'
    if (stream.match(/\b(?:0x[0-9a-fA-F]+|\d+(?:KB|MB|GB)?)\b/)) return 'number'
    stream.next(); return null
  },
})
const highlight = HighlightStyle.define([
  { tag: tags.keyword, color: '#7dd3fc', fontWeight: '700' },
  { tag: tags.bool, color: '#c4b5fd' }, { tag: tags.string, color: '#86efac' },
  { tag: tags.regexp, color: '#fda4af' }, { tag: tags.comment, color: '#64748b', fontStyle: 'italic' },
  { tag: tags.variableName, color: '#fbbf24' }, { tag: tags.number, color: '#fb923c' },
])

function friendlyError(e) {
  return e?.message || String(e)
}
function statusLabel(status) {
  return ({ enabled: '有效', disabled: '已禁用', draft: '草稿' })[status] || status
}
function formatSize(size) {
  return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KiB`
}
async function loadPackages() {
  const data = await listYaraPackages()
  packages.value = data.packages || []
}
async function loadMarket() {
  const data = await listYaraMarket()
  market.value = data.sources || []
}
async function selectTab(id) {
  tab.value = id
  error.value = ''
  if (id === 'market') await run(loadMarket)
}
async function run(fn) {
  busy.value = true; error.value = ''
  try { return await fn() } catch (e) { error.value = friendlyError(e); throw e } finally { busy.value = false }
}
async function createPackage() {
  const name = window.prompt('规则包名称')
  if (!name) return
  await run(async () => {
    const pkg = await createYaraPackage({ name })
    await loadPackages(); emit('packages-changed'); await openEditor(pkg)
  }).catch(() => {})
}
async function togglePackage(pkg, enabled) {
  await run(async () => { await setYaraPackageEnabled(pkg.id, enabled); await loadPackages(); emit('packages-changed') }).catch(() => {})
}
async function removePackage(pkg) {
  if (!window.confirm(`删除规则包“${pkg.name}”及其全部历史？`)) return
  await run(async () => { await deleteYaraPackage(pkg.id); await loadPackages(); emit('packages-changed') }).catch(() => {})
}
async function addCustomSource() {
  await run(async () => {
    await addYaraMarketSource(customSource); Object.assign(customSource, { repository: '', ref: 'main', subdirectory: '' })
    showCustomSource.value = false; await loadMarket()
  }).catch(() => {})
}
async function installSource(source) {
  await run(async () => {
    await installYaraMarketSource(source.id); await Promise.all([loadMarket(), loadPackages()]); emit('packages-changed')
  }).catch(() => {})
}
function chooseZip(event) {
  zipFile.value = event.target.files?.[0] || null
  zipPreview.value = null
}
async function previewZip() {
  if (!zipFile.value) return
  await run(async () => {
    zipPreview.value = await previewYaraZip(zipFile.value)
    entrypointText.value = (zipPreview.value.entrypoints || []).join('\n')
  }).catch(() => {})
}
async function confirmImport(saveAsDraft) {
  await run(async () => {
    const pkg = await confirmYaraZip({
      token: zipPreview.value.token, save_as_draft: saveAsDraft,
      entrypoints: entrypointText.value.split(/\r?\n/).map(v => v.trim()).filter(Boolean),
    })
    zipPreview.value = null; zipFile.value = null; await loadPackages(); emit('packages-changed')
    if (saveAsDraft) await openEditor(pkg); else tab.value = 'installed'
  }).catch(() => {})
}
async function openEditor(pkg) {
  if (pkg.source_type === 'market') {
    pkg = await run(async () => {
      const fork = await forkYaraPackage(pkg.id)
      await loadPackages()
      emit('packages-changed')
      return fork
    }).catch(() => null)
    if (!pkg) return
  }
  editorPackage.value = pkg; tab.value = 'editor'; diagnostics.value = []
  await run(async () => {
    draft.value = await ensureYaraDraft(pkg.id)
    const first = draft.value.files.find(f => f.is_rule)?.path || draft.value.files[0]?.path
    if (first) await openFile(first)
  }).catch(() => {})
}
async function openHistory(pkg) {
  historyPackage.value = pkg; tab.value = 'history'
  await run(async () => { versions.value = (await getYaraVersions(pkg.id)).versions || [] }).catch(() => {})
}
async function openFile(path) {
  if (!editorPackage.value) return
  if (activeFile.value && activeFile.value !== path && dirtyFiles.has(activeFile.value)) {
    await saveFile(activeFile.value)
  }
  if (!(path in buffers)) {
    const data = await getYaraDraftFile(editorPackage.value.id, path)
    buffers[path] = data.content; fileShas[path] = data.sha256
  }
  if (!openFiles.value.includes(path)) openFiles.value.push(path)
  activeFile.value = path
  await nextTick(); mountEditor()
}
async function searchContent() {
  if (!editorPackage.value || !fileSearch.value.trim()) {
    searchHits.value = []
    return
  }
  await run(async () => {
    searchHits.value = (
      await searchYaraDraft(editorPackage.value.id, fileSearch.value.trim())
    ).results || []
  }).catch(() => {})
}
function mountEditor() {
  editorView?.destroy(); editorView = null
  if (!editorHost.value || !activeFile.value) return
  editorView = new EditorView({
    parent: editorHost.value,
    state: EditorState.create({
      doc: buffers[activeFile.value] || '',
      extensions: [
        yaraLanguage, syntaxHighlighting(highlight), EditorView.lineWrapping,
        EditorView.theme({
          '&': { height: '100%', backgroundColor: 'transparent', color: 'var(--text-primary)' },
          '.cm-scroller': { fontFamily: 'var(--font-mono)', fontSize: '12px' },
          '.cm-gutters': { backgroundColor: 'var(--bg-tertiary)', color: 'var(--text-muted)', border: 'none' },
          '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--accent-glow)' },
        }),
        EditorView.updateListener.of(update => {
          if (update.docChanged) {
            buffers[activeFile.value] = update.state.doc.toString()
            dirtyFiles.add(activeFile.value)
            clearTimeout(autosaveTimer)
            const path = activeFile.value
            autosaveTimer = setTimeout(() => {
              if (dirtyFiles.has(path)) saveFile(path)
            }, 1200)
          }
        }),
        keymap.of([{ key: 'Mod-s', preventDefault: true, run: () => { saveFile(); return true } }]),
      ],
    }),
  })
}
function closeFile(path) {
  const index = openFiles.value.indexOf(path)
  openFiles.value = openFiles.value.filter(p => p !== path)
  if (activeFile.value === path) {
    activeFile.value = openFiles.value[Math.max(0, index - 1)] || ''
    nextTick(mountEditor)
  }
}
async function saveFile(path = activeFile.value) {
  if (!path) return
  await run(async () => {
    const result = await saveYaraDraftFile(editorPackage.value.id, {
      path, content: buffers[path],
      base_revision: draft.value.revision, file_sha: fileShas[path] || null,
    })
    draft.value.revision = result.revision; fileShas[path] = result.sha256
    dirtyFiles.delete(path)
    draft.value = await ensureYaraDraft(editorPackage.value.id)
  }).catch(() => {})
}
async function saveAll() {
  for (const path of [...dirtyFiles]) await saveFile(path)
}
async function newFile() {
  const path = window.prompt('新文件路径（相对于包根目录）', 'new-rule.yar')
  if (!path) return
  activeFile.value = path; buffers[path] = 'rule new_rule {\\n    condition:\\n        false\\n}\\n'
  fileShas[path] = ''; dirtyFiles.add(path); if (!openFiles.value.includes(path)) openFiles.value.push(path)
  await saveFile(); await openFile(path)
}
async function renameFile() {
  const next = window.prompt('新路径', activeFile.value)
  if (!next || next === activeFile.value) return
  await saveFile()
  await run(async () => {
    const old = activeFile.value
    draft.value = await renameYaraDraftFile(editorPackage.value.id, old, next, draft.value.revision)
    buffers[next] = buffers[old]; fileShas[next] = fileShas[old]
    delete buffers[old]; delete fileShas[old]
    openFiles.value = openFiles.value.map(p => p === old ? next : p); activeFile.value = next
    await nextTick(); mountEditor()
  }).catch(() => {})
}
async function deleteFile() {
  if (!window.confirm(`删除 ${activeFile.value}？`)) return
  await run(async () => {
    const old = activeFile.value
    draft.value = await deleteYaraDraftFile(editorPackage.value.id, old, draft.value.revision)
    closeFile(old); delete buffers[old]; delete fileShas[old]
  }).catch(() => {})
}
async function validateDraft() {
  await run(async () => {
    await saveAll()
    const result = await validateYaraPackage(editorPackage.value.id)
    diagnostics.value = result.diagnostics || []
    if (result.valid) diagnostics.value = [{ severity: 'success', code: 'valid', message: '整包编译通过' }]
  }).catch(() => {})
}
async function commitDraft() {
  await run(async () => {
    await saveAll()
    await commitYaraPackage(editorPackage.value.id, { base_revision: draft.value.revision })
    diagnostics.value = [{ severity: 'success', code: 'committed', message: '已创建不可变版本并激活' }]
    await loadPackages(); emit('packages-changed')
    editorPackage.value = packages.value.find(p => p.id === editorPackage.value.id) || editorPackage.value
  }).catch(() => {})
}
async function jumpDiagnostic(item) {
  if (!item.file) return
  await openFile(item.file)
  if (!editorView || !item.line) return
  const line = editorView.state.doc.line(
    Math.min(Math.max(1, Number(item.line)), editorView.state.doc.lines),
  )
  const position = Math.min(line.to, line.from + Math.max(0, Number(item.column || 1) - 1))
  editorView.dispatch({
    selection: { anchor: position },
    effects: EditorView.scrollIntoView(position, { y: 'center' }),
  })
  editorView.focus()
}
async function restoreVersion(version) {
  if (!window.confirm(`从 v${version.version} 创建恢复版本？`)) return
  await run(async () => {
    await restoreYaraVersion(historyPackage.value.id, version.id)
    versions.value = (await getYaraVersions(historyPackage.value.id)).versions || []
    await loadPackages(); emit('packages-changed')
  }).catch(() => {})
}
async function showVersionDiff(version) {
  await run(async () => {
    const diff = await getYaraVersionDiff(historyPackage.value.id, version.id)
    const summary = [
      `新增: ${diff.added.join(', ') || '无'}`,
      `修改: ${diff.modified.join(', ') || '无'}`,
      `删除: ${diff.deleted.join(', ') || '无'}`,
    ].join('\n')
    versionDiff.value = summary + '\n\n' + Object.values(diff.diffs || {}).join('\n')
  }).catch(() => {})
}

onMounted(() => run(loadPackages).catch(() => {}))
onBeforeUnmount(() => { clearTimeout(autosaveTimer); editorView?.destroy() })
</script>

<style scoped>
.rc-overlay{position:fixed;inset:0;z-index:1100;display:grid;place-items:center;padding:22px;background:var(--overlay-backdrop);backdrop-filter:blur(8px)}
.rc-modal{width:min(1240px,96vw);height:min(820px,94vh);display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--border);border-radius:18px;background:var(--bg-secondary);box-shadow:var(--shadow-xl)}
.rc-header{display:flex;justify-content:space-between;align-items:center;padding:18px 22px 14px;background:linear-gradient(120deg,var(--bg-tertiary),var(--bg-secondary));border-bottom:1px solid var(--border-subtle)}.rc-header h1{font-size:20px}.rc-header p{color:var(--text-muted);font-size:12px}.rc-kicker{color:var(--accent-bright);font:700 10px var(--font-mono);letter-spacing:.14em}.rc-close{border:0;background:transparent;color:var(--text-muted);cursor:pointer;padding:8px}
.rc-tabs{display:flex;padding:0 18px;border-bottom:1px solid var(--border-subtle);background:var(--bg-tertiary)}.rc-tabs button{padding:12px 15px;border:0;border-bottom:2px solid transparent;background:transparent;color:var(--text-secondary);cursor:pointer}.rc-tabs button.active{border-color:var(--accent);color:var(--accent-bright)}.rc-tabs span{margin-left:6px;padding:1px 5px;border-radius:8px;background:var(--accent-glow);font-size:10px}
.rc-body{position:relative;flex:1;min-height:0;overflow:auto}.rc-section{padding:22px}.rc-toolbar{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:18px}.rc-toolbar h2{font-size:17px}.rc-toolbar p,.rc-card p,.rc-market-item p{color:var(--text-muted);font-size:12px}.rc-toolbar button,.rc-card-actions button,.rc-card-actions a,.rc-editor-actions button,.rc-actions button,.rc-version button,.rc-dropzone button{padding:7px 10px;border:1px solid var(--border);border-radius:7px;background:var(--bg-elevated);color:var(--text-secondary);cursor:pointer;text-decoration:none;font-size:12px}.rc-primary{padding:8px 12px!important;border:1px solid var(--accent-dim)!important;border-radius:8px;background:var(--accent)!important;color:var(--load-btn-text)!important;font-weight:700;cursor:pointer}.rc-primary:disabled{opacity:.5;cursor:not-allowed}
.rc-package-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px}.rc-card{padding:15px;border:1px solid var(--border-subtle);border-radius:12px;background:var(--bg-tertiary)}.rc-card-head{display:flex;justify-content:space-between;gap:10px}.rc-card h3,.rc-market-item h3{font-size:14px}.rc-card code,.rc-market-item code{font-size:11px;color:var(--accent-bright)}.rc-card p{min-height:36px;margin:10px 0}.rc-status{height:max-content;padding:3px 7px;border-radius:10px;font-size:10px;background:var(--bg-elevated)}.rc-status.enabled{color:var(--text-success)}.rc-status.draft{color:var(--text-warning)}.rc-card-meta{display:flex;gap:10px;color:var(--text-muted);font-size:10px}.rc-card-meta .update{color:var(--text-warning)}.rc-card-actions{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:13px}.rc-card-actions .danger{color:var(--text-error)}.rc-switch{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text-secondary)}
.rc-market-list{display:grid;gap:10px}.rc-market-item,.rc-version{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:15px;border:1px solid var(--border-subtle);border-radius:10px;background:var(--bg-tertiary)}.rc-market-item h3 span{padding:2px 5px;border-radius:5px;color:var(--text-success);background:color-mix(in srgb,var(--text-success) 12%,transparent);font-size:9px}.rc-inline-form{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:8px;margin:-6px 0 16px}.rc-inline-form input{min-width:0;padding:8px;border:1px solid var(--border);border-radius:7px;background:var(--bg-primary);color:var(--text-primary)}
.rc-import{display:grid;grid-template-columns:minmax(300px,.8fr) 1.2fr;gap:18px}.rc-dropzone{display:flex;min-height:280px;flex-direction:column;align-items:center;justify-content:center;gap:10px;border:1px dashed var(--border);border-radius:14px;background:var(--bg-tertiary);text-align:center}.rc-dropzone.ready{border-color:var(--accent)}.rc-dropzone p{max-width:380px;color:var(--text-muted)}.rc-preview{padding:18px;border:1px solid var(--border-subtle);border-radius:14px}.rc-preview-title{display:flex;justify-content:space-between}.rc-preview-title .valid{color:var(--text-success)}.rc-preview-title .invalid{color:var(--text-error)}.rc-preview>p{margin:8px 0 14px;color:var(--text-muted)}.rc-entrypoints{display:grid;gap:5px;color:var(--text-muted);font-size:11px}.rc-entrypoints textarea{resize:vertical;padding:8px;border:1px solid var(--border);border-radius:7px;background:var(--bg-primary);color:var(--text-primary);font:11px var(--font-mono)}.rc-actions{display:flex;gap:8px;margin-top:12px}
.rc-editor-section{height:100%;display:flex;min-height:0}.rc-file-panel{width:230px;flex-shrink:0;overflow:auto;border-right:1px solid var(--border-subtle);background:var(--bg-tertiary)}.rc-file-head{display:flex;justify-content:space-between;align-items:center;padding:12px;border-bottom:1px solid var(--border-subtle)}.rc-file-head div{display:flex;flex-direction:column}.rc-file-head small{color:var(--text-muted)}.rc-file-head button{border:0;background:transparent;color:var(--accent-bright)}.rc-file-search{width:calc(100% - 16px);margin:8px;padding:7px;border:1px solid var(--border-subtle);border-radius:6px;background:var(--bg-primary);color:var(--text-primary);font-size:11px}.rc-search-hit{width:calc(100% - 16px);display:grid;gap:2px;margin:0 8px 5px;padding:6px;border:1px solid var(--border-subtle);border-radius:5px;background:var(--bg-primary);color:var(--accent-bright);text-align:left;font:10px var(--font-mono)}.rc-search-hit small{overflow:hidden;color:var(--text-muted);text-overflow:ellipsis;white-space:nowrap}.rc-file{width:100%;display:flex;justify-content:space-between;gap:7px;padding:8px 11px;border:0;background:transparent;color:var(--text-secondary);cursor:pointer;text-align:left;font:11px var(--font-mono)}.rc-file.active{background:var(--accent-glow);color:var(--accent-bright)}.rc-file small{color:var(--text-muted)}
.rc-workbench{flex:1;min-width:0;display:flex;flex-direction:column}.rc-editor-toolbar{display:flex;justify-content:space-between;border-bottom:1px solid var(--border-subtle);background:var(--bg-tertiary)}.rc-open-tabs{display:flex;min-width:0;overflow:auto}.rc-open-tabs button{display:flex;align-items:center;gap:7px;padding:9px 10px;border:0;border-right:1px solid var(--border-subtle);background:transparent;color:var(--text-muted);font:11px var(--font-mono);white-space:nowrap}.rc-open-tabs button.active{background:var(--bg-secondary);color:var(--text-primary)}.rc-open-tabs i{color:var(--text-warning)}.rc-open-tabs b{font:14px var(--font-sans)}.rc-editor-actions{display:flex;align-items:center;gap:5px;padding:5px;white-space:nowrap}.rc-editor-actions button{padding:5px 7px}.rc-editor-actions kbd{font-size:9px;color:var(--text-muted)}.rc-editor{flex:1;min-height:180px;overflow:hidden}.rc-diagnostics{max-height:150px;overflow:auto;border-top:1px solid var(--border-subtle)}
.rc-version div{display:flex;align-items:center;gap:12px}.rc-version span,.rc-version small{color:var(--text-muted)}.rc-empty{display:grid;min-height:180px;place-items:center;color:var(--text-muted)}.rc-loading{position:absolute;top:10px;right:20px;z-index:3;padding:6px 10px;border-radius:8px;background:var(--accent);color:var(--load-btn-text);font-size:11px}.rc-alert{margin:10px 22px -10px;padding:8px 10px;border-radius:7px}.rc-alert.error{color:var(--text-error);background:color-mix(in srgb,var(--text-error) 10%,transparent)}
.rc-version .rc-version-actions{gap:6px}.rc-version-diff{max-height:360px;overflow:auto;margin-top:12px;padding:12px;border:1px solid var(--border-subtle);border-radius:8px;background:var(--bg-primary);color:var(--text-secondary);font:11px/1.5 var(--font-mono);white-space:pre-wrap}
@media(max-width:800px){.rc-overlay{padding:0}.rc-modal{width:100vw;height:100vh;border-radius:0}.rc-import{grid-template-columns:1fr}.rc-file-panel{width:150px}.rc-editor-actions button:not(.rc-primary){display:none}.rc-inline-form{grid-template-columns:1fr}}
</style>
