// Run with node tests/test_post_text_ui.js. Exercises the real editor reconciliation helpers.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const start = html.indexOf("function postText(");
const end = html.indexOf("/* ============================================================ clip editor */", start);
const helpers = html.slice(start, end);

function fixture() {
  const values = {title:"English title", description:"English description\n\n#old", hashtags:"old"};
  const fields = Object.fromEntries(Object.entries(values).map(([k, value]) => [k, {value}]));
  const editor = {root:{fields}, jid:"job1", cid:"clip1", segments:[[0, 3], [6, 9]], postSaved:{...values}};
  const clip = {id:"clip1", status:"done", version:1};
  const context = vm.createContext({D:editor, jobOf:() => ({clips:[clip]}),
    $:(selector, root) => root.fields[/data-f="([^"]+)"/.exec(selector)[1]]});
  vm.runInContext(helpers, context);
  return {editor, clip, fields, context};
}

{
  const {editor, fields, context} = fixture();
  context.syncPostText(editor, {title:"Título novo", description:"Descrição nova\n\n#novo", hashtags:["novo"]});
  assert.equal(fields.title.value, "Título novo");
  assert.equal(fields.description.value, "Descrição nova\n\n#novo");
  assert.equal(fields.hashtags.value, "novo");
}
{
  const {editor, fields, context} = fixture();
  fields.title.value = "My unsaved title";
  context.syncPostText(editor, {title:"Título novo", description:"Descrição nova", hashtags:["novo"]});
  assert.equal(fields.title.value, "My unsaved title");
  assert.equal(fields.description.value, "Descrição nova");
  const submitted = context.postText(editor);
  fields.title.value = "New edit while saving";
  context.syncPostText(editor, {title:"My unsaved title", description:"Descrição nova\n\n#novo", hashtags:["novo"]}, submitted);
  assert.equal(fields.title.value, "New edit while saving");
  assert.equal(fields.description.value, "Descrição nova\n\n#novo");
}
{
  const {editor, clip, fields, context} = fixture();
  const revision = context.postTextRevision(editor);
  assert.equal(context.canApplyPostRewrite(editor, revision), true);
  editor.segments.reverse();
  assert.equal(context.canApplyPostRewrite(editor, revision), false);
  editor.segments.reverse();
  fields.description.value = "User typed while the AI was running";
  assert.equal(context.canApplyPostRewrite(editor, revision), false);
  fields.description.value = editor.postSaved.description;
  clip.version++;
  assert.equal(context.canApplyPostRewrite(editor, revision), false);
  clip.version--;
  clip.status = "queued";
  assert.equal(context.canApplyPostRewrite(editor, revision), false);
  clip.status = "done";
  editor.sending = true;
  assert.equal(context.canApplyPostRewrite(editor, revision), false);
  editor.sending = false;
  context.D = {};
  assert.equal(context.canApplyPostRewrite(editor, revision), false);
}
console.log("PASS saved text reconciliation, unsaved edits, normalized save response and stale AI rewrites");
