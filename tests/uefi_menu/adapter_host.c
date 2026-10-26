#include "Internal.h"
static UINT32 gpio_level, gpio_writes, bad_power, short_read, io_status, close_fail;
static UINTN delay_calls, open_calls;
static UINT32 mmio_read(UINTN a)
{
    if (a==0x03932000 || a==0x03d59000) return 0x203;
    if (a==0x03932004) return 3;
    assert(a==0x03d59004); return gpio_level;
}
static void mmio_write(UINTN a, UINT32 v)
{ assert(a==0x03d59004); gpio_writes++; gpio_level=v|1; }
#define CHIRON_MENU_MMIO_READ(a) mmio_read(a)
#define CHIRON_MENU_MMIO_WRITE(a,v) mmio_write(a,v)
#include "../../ci/uefi/ChironMenuTouch.c"

EFI_GUID gEfiSimpleTextInProtocolGuid, gEfiDevicePathProtocolGuid;
EFI_GUID gEfiGraphicsOutputProtocolGuid;
static EFI_STATUS stall(UINTN us)
{ assert(us==5000 || us==200000); delay_calls++; return 0; }
static EFI_BOOT_SERVICES services={.Stall=stall};
EFI_BOOT_SERVICES *gBS=&services;
EFI_SYSTEM_TABLE *gST;
void UniXNULog(const CHAR8 *s, ...) { (void)s; }
UINTN UnicodeSPrint(CHAR16 *s, UINTN n, const CHAR16 *fmt, ...)
{ (void)fmt; if(n) *s=0; return 0; }
static EFI_STATUS level(UINT32 pmic, UINT32 id, UINT32 *uv)
{ assert(!pmic); *uv=id==5 ? 1848000 : 1880000; if(bad_power) (*uv)--; return 0; }
static EFI_STATUS state(UINT32 pmic, UINT32 id, MENU_VREG_STATE *s)
{ (void)pmic; (void)id; s->Enabled=s->Ok=1; return 0; }
static UINT32 spmi(MENU_SPMI *p, UINT32 sid, UINT32 pri, UINT32 reg,
                   UINT8 *d, UINT32 n, UINT32 *count)
{
    (void)p; assert(sid==1 && pri==0 && n==2);
    assert(reg==0x4540 || reg==0x4d40);
    d[0]=reg==0x4540 ? 0x38 : 0x58; d[1]=7; *count=2; return 0;
}
static UINT32 read_i2c(VOID *h, MENU_I2C_CONFIG *c, UINT16 reg, UINT16 off,
                       UINT8 *data, UINT16 n, UINT32 *count, UINT32 timeout)
{
    assert(h && c->Frequency==400 && c->Address==0x20 && !c->AddressType && off==1 && timeout==100);
    assert(reg<0xff && n<=80); memset(data,1,n);
    *count=short_read ? n-1 : n; return io_status;
}
static UINT32 write_i2c(VOID *h, MENU_I2C_CONFIG *c, UINT16 reg, UINT16 off,
                        UINT8 *data, UINT16 n, UINT32 *count, UINT32 timeout)
{
    assert(h && c->Frequency==400 && c->Address==0x20 && !c->AddressType && off==1 && timeout==100);
    assert(reg==0xff && n==1 && *data<5); *count=1; return io_status;
}
static UINT32 close_i2c(VOID *h) { assert(h); return close_fail ? 3 : 0; }
static UINT32 open_i2c(UINT32 n, VOID **h) { (void)n; (void)h; open_calls++; return 0; }
static MENU_I2C i2c={.Revision=0x10000,.Open=open_i2c,.Read=read_i2c,
                    .Write=write_i2c,.Close=close_i2c};
static MENU_VREG vreg={.GetLevel=level,.GetStatus=state};
static MENU_SPMI pmic={.Read=spmi};
static int active, fail_poll;
void chiron_dsx_stop(struct chiron_dsx *d) { d->ready=0; }
void chiron_dsx_init(struct chiron_dsx *d,const struct chiron_dsx_bus *b,
                     chiron_dsx_post post,void *c)
{ (void)d; (void)b; (void)post; (void)c; }
int chiron_dsx_start(struct chiron_dsx *d) { (void)d; return 0; }
int chiron_dsx_poll(struct chiron_dsx *d)
{
    memset(d->contact,0,sizeof(d->contact));
    d->layout.contacts=10;
    if(active) d->contact[0]=(struct chiron_dsx_contact){20000,30000,1};
    if(active>1) d->contact[4]=(struct chiron_dsx_contact){40000,50000,1};
    return fail_poll ? -2 : 0;
}
int chiron_f12_prepare(void *c,const struct chiron_dsx_functions *f,struct chiron_dsx_layout *l)
{ (void)c; (void)f; (void)l; return 0; }
int chiron_f12_validate(void *c,const struct chiron_dsx_functions *f,struct chiron_dsx_layout *l)
{ (void)c; (void)f; (void)l; return 0; }
int main(void)
{
    mI2c=&i2c; mVreg=&vreg; mSpmi=&pmic; mHandle=(void *)1;
    assert(ReleaseReset() && gpio_writes==1 && delay_calls==2);
    assert(ReleaseReset() && gpio_writes==1);
    bad_power=1; assert(!ReleaseReset() && gpio_writes==1); bad_power=0;
    UINT8 data[8];
    assert(!SelectPage(NULL,0x20,2) && mPage==2);
    assert(ReadRegister(NULL,0x20,8,data,8)==8);
    short_read=1; assert(ReadRegister(NULL,0x20,8,data,8)==-2); short_read=0;
    io_status=17; assert(SelectPage(NULL,0x20,0)==-2 && mTransport==17); io_status=0;
    assert(SelectPage(NULL,0x21,0)==CHIRON_DSX_INVALID);
    EFI_ABSOLUTE_POINTER_STATE s;
    mReady=TRUE; active=1;
    assert(!ChironMenuTouchPoll(&s) && s.ActiveButtons==1 && s.CurrentX==20000);
    active=2; assert(!ChironMenuTouchPoll(&s) && !s.ActiveButtons && s.CurrentY==65535);
    active=1; assert(ChironMenuTouchPoll(&s)==EFI_NOT_READY);
    active=0; assert(ChironMenuTouchPoll(&s)==EFI_NOT_READY);
    active=1; assert(!ChironMenuTouchPoll(&s) && s.ActiveButtons);
    active=0; assert(!ChironMenuTouchPoll(&s) && !s.ActiveButtons && s.CurrentX==20000);
    fail_poll=1; assert(ChironMenuTouchPoll(&s)==EFI_DEVICE_ERROR && !mReady);
    close_fail=1; assert(ChironMenuTouchStop()==EFI_DEVICE_ERROR && mHandle);
    close_fail=0; assert(!ChironMenuTouchStop() && !mHandle);
    assert(!ChironMenuTouchStop() && !open_calls);
    return 0;
}
