#include "Internal.h"
#include "ChironMenuTouch.h"

EFI_GUID gEfiSimpleTextInProtocolGuid, gEfiDevicePathProtocolGuid;
EFI_GUID gEfiGraphicsOutputProtocolGuid;
static int scenario, tick, stop_calls, touch_calls;
static EFI_STATUS read_key(TEXT_IN *in, EFI_INPUT_KEY *key)
{
    (void)in;
    *key = (EFI_INPUT_KEY){0};
    if (scenario == 5 && tick == 1) key->ScanCode = SCAN_DOWN;
    else key->UnicodeChar = L'\r';
    return 0;
}
static EFI_STATUS locate(UINTN a, EFI_GUID *b, VOID *c, UINTN *n, EFI_HANDLE **h)
{
    (void)a; (void)b; (void)c;
    *n = 1; *h = malloc(sizeof(**h)); return 0;
}
static EFI_STATUS handle(EFI_HANDLE a, EFI_GUID *b, VOID **c)
{
    (void)a; (void)b; *c = (void *)1; return 0;
}
static EFI_STATUS create(UINT32 a, UINTN b, VOID *c, VOID *d, EFI_EVENT *e)
{
    (void)a; (void)b; (void)c; (void)d; *e = (void *)1; return 0;
}
static EFI_STATUS timer(EFI_EVENT a, UINTN b, UINT64 c)
{
    (void)a; (void)b; (void)c; return 0;
}
static EFI_STATUS wait_event(UINTN n, EFI_EVENT *events, UINTN *signaled)
{
    (void)n; (void)events;
    assert(++tick < 500);
    *signaled = 0;
    if (scenario == 5 || ((scenario == 1 || scenario == 4) && tick == 6) ||
        ((scenario == 3 || scenario == 6) && tick == 60)) *signaled = 1;
    return 0;
}
static EFI_STATUS close_event(EFI_EVENT e) { (void)e; return 0; }
static EFI_BOOT_SERVICES services = {
    .LocateHandleBuffer=locate, .HandleProtocol=handle,
    .CreateEvent=create, .SetTimer=timer, .WaitForEvent=wait_event, .CloseEvent=close_event
};
static TEXT_IN input = {.ReadKeyStroke=read_key, .WaitForKey=(void *)2};
static EFI_SYSTEM_TABLE system_table;
EFI_BOOT_SERVICES *gBS = &services;
EFI_SYSTEM_TABLE *gST = &system_table;
void UniXNULog(const CHAR8 *s, ...) { (void)s; }
UINTN UnicodeSPrint(CHAR16 *s, UINTN n, const CHAR16 *fmt, ...)
{ (void)fmt; if (n) *s=0; return 0; }
void UniXNUUiDrawFrame(UNIXNU_UI *u, UNIXNU_CONFIG *c) { (void)u; (void)c; }
void UniXNUUiDrawEntry(UNIXNU_UI *u, UNIXNU_CONFIG *c, UINTN i, BOOLEAN a)
{ (void)u; (void)c; (void)i; (void)a; }
void UniXNUUiDrawStatus(UNIXNU_UI *u, UINTN i, const CHAR16 *s)
{ (void)u; (void)i; (void)s; }
EFI_STATUS ChironMenuTouchStart(void)
{ return scenario == 2 || scenario == 5 ? EFI_UNSUPPORTED : 0; }
EFI_STATUS ChironMenuTouchStop(void)
{ return scenario == 4 && ++stop_calls == 1 ? EFI_DEVICE_ERROR : 0; }
void ChironMenuTouchStatus(CHAR16 *s, UINTN n) { if (n) *s=0; }
void ChironMenuTouchFrameStatus(CHAR16 *s, UINTN n) { if (n) *s=0; }
EFI_STATUS ChironMenuTouchPoll(EFI_ABSOLUTE_POINTER_STATE *s)
{
    int call = ++touch_calls;
    if (scenario == 3) return EFI_DEVICE_ERROR;
    if (call > 3) return EFI_NOT_READY;
    *s = (EFI_ABSOLUTE_POINTER_STATE){32768, 22766, 0, call < 3};
    if (scenario == 1 && call >= 2) s->CurrentY=0;
    if (scenario == 6) s->CurrentY=0;
    if (call == 2) s->CurrentX+=500;
    return 0;
}
#include "../../ci/uefi/Menu.c"
int main(int argc, char **argv)
{
    assert(argc == 2);
    scenario=atoi(argv[1]);
    system_table.ConIn = scenario == 2 ? NULL : &input;
    UNIXNU_ENTRY entries[2]={{L"one"},{L"two"}};
    UNIXNU_CONFIG config={.EntryCount=2,.Timeout=1,.FallbackTimeout=1,.Entries=entries};
    UNIXNU_UI ui={UniXNUUiGraphics,1080,2160,4,600,100,100,880,2};
    UINTN selected=UniXNUMenuRun(&ui,&config,TRUE);
    if (scenario == 0) { assert(selected==1 && tick==3); }
    if (scenario == 1 || scenario == 4) { assert(selected==1 && tick==6); }
    if (scenario == 2) { assert(selected==0 && tick==50); }
    if (scenario == 3 || scenario == 6) { assert(selected==0 && tick==60); }
    if (scenario == 5) { assert(selected==1 && tick==2); }
    return 0;
}
