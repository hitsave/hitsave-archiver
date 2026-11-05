/* Sector descramble (from libdvdcss dvdcss_unscramble, GPL-2). */
#include <stdint.h>

#define DVD_KEY_SIZE 5
#define DVDCSS_BLOCK_SIZE 2048

#include "csstables.h"

int hitsave_css_unscramble(uint8_t *p_key, uint8_t *p_sec)
{
    unsigned int i_t1, i_t2, i_t3, i_t4, i_t5, i_t6;
    uint8_t *p_end = p_sec + DVDCSS_BLOCK_SIZE;

    if (!(p_sec[0x14] & 0x30)) {
        return 0;
    }

    i_t1 = (p_key[0] ^ p_sec[0x54]) | 0x100;
    i_t2 = p_key[1] ^ p_sec[0x55];
    i_t3 = (p_key[2] | (p_key[3] << 8) | (p_key[4] << 16))
           ^ (p_sec[0x56] | (p_sec[0x57] << 8) | (p_sec[0x58] << 16));
    i_t4 = i_t3 & 7;
    i_t3 = i_t3 * 2 + 8 - i_t4;
    p_sec += 0x80;
    i_t5 = 0;

    while (p_sec != p_end) {
        i_t4 = p_css_tab2[i_t2] ^ p_css_tab3[i_t1];
        i_t2 = i_t1 >> 1;
        i_t1 = ((i_t1 & 1) << 8) ^ i_t4;
        i_t4 = p_css_tab5[i_t4];
        i_t6 = (((((((i_t3 >> 3) ^ i_t3) >> 1) ^ i_t3) >> 8) ^ i_t3) >> 5) & 0xff;
        i_t3 = (i_t3 << 8) | i_t6;
        i_t6 = p_css_tab4[i_t6];
        i_t5 += i_t6 + i_t4;
        *p_sec = p_css_tab1[*p_sec] ^ (i_t5 & 0xff);
        p_sec++;
        i_t5 >>= 8;
    }

    return 0;
}
